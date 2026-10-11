"""Flujo de presentación de ofertas en SECOP II con Playwright.

Los selectores se pueden ajustar sin tocar código en ~/.config/secop-bot/selectores.json
(se mezcla con los valores por defecto). Cada paso prueba varios candidatos en todos
los frames de la página, porque SECOP II usa ventanas emergentes dentro de iframes.
Se guarda una captura de cada paso en ~/.config/secop-bot/evidencias/<proceso>/.
"""

import json
import re
from datetime import datetime

from .config import APP_DIR
from .ofertas import EVIDENCIAS_DIR, Oferta

SELECTORES_FILE = APP_DIR / "selectores.json"

SELECTORES_DEFECTO: dict = {
    "login_url": "https://community.secop.gov.co/STS/Users/Login/Index",
    "sesion_activa": ["text=/cerrar sesi[oó]n/i", "a[href*='Logout' i]", "#lnkLogout"],
    "login_usuario": ["input[name*='user' i]:visible", "input[id*='user' i]:visible", "input[type=text]:visible"],
    "login_clave": ["input[type=password]:visible"],
    "login_entrar": ["button:has-text('Entrar')", "input[value='Entrar']", "button:has-text('Iniciar')",
                     "input[type=submit]:visible"],
    "presentar_desde_detalle": ["a:has-text('Presentar oferta')", "button:has-text('Presentar oferta')",
                                "input[value*='Presentar oferta' i]", "a:has-text('Crear oferta')",
                                "button:has-text('Crear oferta')"],
    "campos_precio": ["input[id*='Price' i]:visible", "input[id*='Precio' i]:visible",
                      "input[id*='Valor' i][type=text]:visible"],
    "anexar_documento": ["a:has-text('Anexar documento')", "button:has-text('Anexar documento')",
                         "input[value*='Anexar documento' i]"],
    "input_archivo": ["input[type=file]"],
    "confirmar_anexo": ["button:text-is('Anexar')", "input[value='Anexar']", "a:text-is('Anexar')"],
    "cerrar_popup": ["button:text-is('Cerrar')", "input[value='Cerrar']", "a:text-is('Cerrar')"],
    "requiere_firma": ["button:has-text('Firmar')", "input[value*='Firmar' i]", "a:has-text('Firmar')"],
    "presentar_final": ["button:has-text('Presentar oferta')", "input[value*='Presentar' i]",
                        "a:has-text('Presentar oferta')", "button:has-text('Presentar')"],
    "confirmar_envio": ["button:has-text('Confirmar')", "button:has-text('Sí')", "button:has-text('Aceptar')",
                        "input[value='Confirmar']", "input[value='Aceptar']", "input[value='Sí']"],
    "exito": ["text=/oferta (fue )?(presentada|enviada)/i", "text=/presentad[ao] con [ée]xito/i"],
}


class ErrorEnvio(Exception):
    pass


def cargar_selectores() -> dict:
    sel = json.loads(json.dumps(SELECTORES_DEFECTO))
    if SELECTORES_FILE.exists():
        try:
            sel.update(json.loads(SELECTORES_FILE.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as e:
            raise ErrorEnvio(f"selectores.json inválido: {e}") from e
    else:
        SELECTORES_FILE.parent.mkdir(parents=True, exist_ok=True)
        SELECTORES_FILE.write_text(json.dumps(SELECTORES_DEFECTO, indent=2, ensure_ascii=False), encoding="utf-8")
    return sel


def _pesos(valor: float) -> str:
    return "$" + f"{valor:,.0f}".replace(",", ".")


class FlujoOferta:
    """Pasos del envío. `esperar_captcha(page)` y `confirmar(resumen) -> bool` los pone el navegador."""

    def __init__(self, page, oferta: Oferta, documentos, modo: str, credenciales: tuple[str, str],
                 log, esperar_captcha, confirmar):
        self.page = page
        self.oferta = oferta
        self.documentos = documentos
        self.modo = modo
        self.usuario, self.clave = credenciales
        self.log = log
        self.esperar_captcha = esperar_captcha
        self.confirmar = confirmar
        self.sel = cargar_selectores()
        self.evidencias = EVIDENCIAS_DIR / re.sub(r"[^\w.\-]+", "_", oferta.proceso_id)
        self.evidencias.mkdir(parents=True, exist_ok=True)
        self._n = 0

    # ------------------------------------------------------------ utilidades
    def _captura(self, nombre: str) -> None:
        self._n += 1
        marca = datetime.now().strftime("%H%M%S")
        try:
            self.page.screenshot(path=str(self.evidencias / f"{self._n:02d}_{marca}_{nombre}.png"), full_page=True)
        except Exception:
            pass

    def _buscar(self, clave: str, timeout_ms: int = 0, frame_preferido=None):
        """Primer elemento visible que coincida con algún candidato, en cualquier frame
        (empezando por `frame_preferido`, p. ej. el de la ventana emergente)."""
        espera = max(timeout_ms, 1)
        transcurrido = 0
        while True:
            frames = self.page.frames
            if frame_preferido is not None:
                frames = [frame_preferido] + [f for f in frames if f != frame_preferido]
            for frame in frames:
                for selector in self.sel[clave]:
                    try:
                        loc = frame.locator(selector)
                        for i in range(min(loc.count(), 10)):
                            el = loc.nth(i)
                            if el.is_visible():
                                return el
                    except Exception:
                        continue
            if transcurrido >= espera:
                return None
            self.page.wait_for_timeout(500)
            transcurrido += 500

    def _todos(self, clave: str) -> list:
        encontrados, vistos = [], set()
        for frame in self.page.frames:
            for selector in self.sel[clave]:
                try:
                    loc = frame.locator(selector)
                    for i in range(min(loc.count(), 50)):
                        el = loc.nth(i)
                        ident = (frame.url, el.get_attribute("id") or el.get_attribute("name") or f"{selector}{i}")
                        if ident not in vistos and el.is_visible():
                            vistos.add(ident)
                            encontrados.append(el)
                except Exception:
                    continue
        return encontrados

    def _click(self, clave: str, timeout_ms: int = 15000, obligatorio: bool = True, frame_preferido=None) -> bool:
        el = self._buscar(clave, timeout_ms, frame_preferido)
        if el is None:
            if obligatorio:
                self._captura(f"falta_{clave}")
                raise ErrorEnvio(f"No se encontró el elemento «{clave}». Ajusta {SELECTORES_FILE}.")
            return False
        el.click()
        self.page.wait_for_load_state("domcontentloaded")
        self.page.wait_for_timeout(1200)
        self.esperar_captcha(self.page)
        return True

    # ----------------------------------------------------------------- pasos
    def sesion_activa(self) -> bool:
        return self._buscar("sesion_activa", 1500) is not None

    def iniciar_sesion(self) -> None:
        self.log("Iniciando sesión en SECOP II…")
        self.page.goto(self.sel["login_url"], wait_until="domcontentloaded", timeout=90_000)
        self.esperar_captcha(self.page)
        if self.usuario and self.clave:
            u, c = self._buscar("login_usuario", 10000), self._buscar("login_clave", 2000)
            if u and c:
                u.fill(self.usuario)
                c.fill(self.clave)
                self.esperar_captcha(self.page)
                self._click("login_entrar", obligatorio=False)
        if not self.sesion_activa():
            self.log("Completa el inicio de sesión en la ventana del navegador (tienes 10 minutos)…")
            if self._buscar("sesion_activa", 600_000) is None:
                raise ErrorEnvio("No se inició sesión en SECOP II.")
        self._captura("sesion")

    def ejecutar(self) -> tuple[str, str]:
        """Devuelve (estado, detalle) con estado en: lista | enviada."""
        o = self.oferta
        self.page.goto(o.url, wait_until="domcontentloaded", timeout=90_000)
        self.esperar_captcha(self.page)
        if not self.sesion_activa():
            self.iniciar_sesion()
            self.page.goto(o.url, wait_until="domcontentloaded", timeout=90_000)
            self.esperar_captcha(self.page)
        self._captura("detalle")

        self.log("Abriendo el formulario de oferta…")
        self._click("presentar_desde_detalle", 20000)
        self._captura("formulario")

        self._llenar_precio()
        self._anexar_documentos()
        self._captura("oferta_completa")

        if self._buscar("requiere_firma", 1500) is not None:
            raise ErrorEnvio("La oferta requiere firma digital: fírmala y preséntala manualmente (quedó guardada en SECOP).")

        resumen = (
            f"{o.nombre}\n{o.entidad}\nValor ofertado: {_pesos(o.valor_oferta)} "
            f"(precio base {_pesos(o.precio_base)})\nDocumentos: {len(self.documentos)}"
        )
        if self.modo == "simulacion":
            return "lista", "Simulación: formulario completo, no se presentó. Revisa la ventana y las evidencias."
        if self.modo == "confirmar" and not self.confirmar(resumen):
            return "lista", "Envío cancelado por el usuario; la oferta quedó en borrador en SECOP."

        self.log("Presentando la oferta…")
        self._click("presentar_final", 15000)
        self._captura("presentar")
        self._click("confirmar_envio", 8000, obligatorio=False)
        self._captura("confirmacion")
        if self._buscar("exito", 30000) is None:
            raise ErrorEnvio("No apareció la confirmación de SECOP. Verifica la oferta manualmente (ver evidencias).")
        self._captura("enviada")
        return "enviada", f"Oferta presentada {datetime.now():%Y-%m-%d %H:%M:%S}"

    def _llenar_precio(self) -> None:
        campos = self._todos("campos_precio")
        if len(campos) == 1:
            campos[0].fill(str(int(self.oferta.valor_oferta)))
            self.log(f"Valor ofertado: {_pesos(self.oferta.valor_oferta)}")
        elif not campos:
            raise ErrorEnvio("No se encontró el campo de precio en el cuestionario de la oferta.")
        else:
            # Varios ítems/lotes: repartir un valor total sería adivinar. Se detiene.
            raise ErrorEnvio(
                f"El cuestionario tiene {len(campos)} campos de precio (varios ítems o lotes): "
                "complétalos manualmente; la oferta quedó abierta en el navegador."
            )

    def _anexar_documentos(self) -> None:
        if not self.documentos:
            raise ErrorEnvio("No hay documentos para anexar (biblioteca y carpeta del proceso vacías).")
        rutas = [str(d) for d in self.documentos]
        for i, ruta in enumerate(rutas, 1):
            self._click("anexar_documento", 15000)
            entrada, frame_popup = None, None
            for _ in range(20):  # la ventana emergente puede tardar en cargar
                for frame in self.page.frames:
                    for selector in self.sel["input_archivo"]:
                        loc = frame.locator(selector)
                        if loc.count():
                            entrada, frame_popup = loc.first, frame
                            break
                    if entrada:
                        break
                if entrada:
                    break
                self.page.wait_for_timeout(500)
            if entrada is None:
                raise ErrorEnvio("No se encontró el selector de archivos en la ventana «Anexar documento».")
            entrada.set_input_files(ruta)
            self.page.wait_for_timeout(1500)
            self._click("confirmar_anexo", 10000, obligatorio=False, frame_preferido=frame_popup)
            self.page.wait_for_timeout(2500)  # subida
            self._click("cerrar_popup", 5000, obligatorio=False, frame_preferido=frame_popup)
            self.log(f"Documento {i}/{len(rutas)} anexado: {ruta.rsplit('/', 1)[-1]}")
