"""Navegador asistido con Playwright.

Abre los procesos en SECOP II en una ventana visible con un perfil persistente.
Si SECOP muestra el captcha, el bot espera a que el usuario lo resuelva manualmente
y después continúa (por ejemplo, descargando los documentos del proceso).
"""

import queue
import re
import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .config import BROWSER_DIR, DOWNLOAD_DIR
from .envio import ErrorEnvio, FlujoOferta

ESPERA_CAPTCHA_SEG = 600
PATRON_DESCARGA = re.compile(r"descargar|download", re.I)


def _hay_captcha(page) -> bool:
    if "recaptcha" in page.url.lower():
        return True
    return any("recaptcha" in f.url or "hcaptcha" in f.url for f in page.frames if f.url != "about:blank")


def _nombre_seguro(texto: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", texto).strip("_") or "proceso"


class NavegadorAsistido(QThread):
    """Hilo dueño del navegador: Playwright (sync) debe usarse siempre desde el mismo hilo."""

    estado = Signal(str)
    captcha = Signal(bool)  # True: esperando al usuario; False: resuelto
    descargas = Signal(str, list)  # carpeta, archivos
    oferta_resultado = Signal(str, str, str)  # proceso_id, estado, detalle
    pedir_confirmacion = Signal(str, str)  # proceso_id, resumen

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cola: queue.Queue = queue.Queue()
        self._respuesta = threading.Event()
        self._aceptado = False

    # --- API pública (cualquier hilo) ---
    def abrir(self, url: str, proceso_id: str, descargar: bool = False) -> None:
        self._cola.put(("abrir", url, proceso_id, descargar))
        if not self.isRunning():
            self.start()

    def ofertar(self, oferta, documentos, modo: str, credenciales: tuple[str, str]) -> None:
        self._cola.put(("ofertar", oferta, documentos, modo, credenciales))
        if not self.isRunning():
            self.start()

    def responder(self, aceptar: bool) -> None:
        """Respuesta del usuario a `pedir_confirmacion` (desde la interfaz)."""
        self._aceptado = aceptar
        self._respuesta.set()

    def cerrar(self) -> None:
        self._cola.put(("salir",))

    # --- hilo del navegador ---
    def run(self) -> None:
        try:
            from playwright.sync_api import Error as PwError
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.estado.emit("Playwright no está instalado: pip install playwright && playwright install chromium")
            return

        BROWSER_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with sync_playwright() as pw:
                ctx = pw.chromium.launch_persistent_context(
                    str(BROWSER_DIR), headless=False, accept_downloads=True, viewport=None
                )
                try:
                    while True:
                        orden = self._cola.get()
                        if orden[0] == "salir":
                            break
                        try:
                            if orden[0] == "ofertar":
                                self._ofertar(ctx, *orden[1:])
                            else:
                                self._procesar(ctx, *orden[1:])
                        except PwError as e:
                            if "closed" in str(e).lower():
                                self.estado.emit("Se cerró la ventana del navegador.")
                                break
                            self.estado.emit(f"Error del navegador: {e}")
                finally:
                    try:
                        ctx.close()
                    except Exception:
                        pass
        except Exception as e:
            self.estado.emit(f"No se pudo iniciar el navegador: {e}")

    def _procesar(self, ctx, url: str, pid: str, descargar: bool) -> None:
        page = ctx.new_page()
        self.estado.emit(f"Abriendo {pid}…")
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        page.wait_for_timeout(1500)

        if not self._esperar_captcha(page):
            return
        self.estado.emit(f"Proceso {pid} abierto.")
        if descargar:
            self._descargar_documentos(page, pid)

    def _esperar_captcha(self, page) -> bool:
        """Si hay captcha, espera a que el usuario lo resuelva. False si se agota el tiempo."""
        if not _hay_captcha(page):
            return True
        self.captcha.emit(True)
        self.estado.emit("Resuelve el captcha en la ventana del navegador…")
        try:
            page.bring_to_front()
        except Exception:
            pass
        limite = time.monotonic() + ESPERA_CAPTCHA_SEG
        while _hay_captcha(page):
            if time.monotonic() > limite:
                self.captcha.emit(False)
                self.estado.emit("Tiempo de espera del captcha agotado.")
                return False
            page.wait_for_timeout(1000)
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)
        self.captcha.emit(False)
        return True

    def _ofertar(self, ctx, oferta, documentos, modo: str, credenciales) -> None:
        pid = oferta.proceso_id
        page = ctx.new_page()

        def esperar(pg):
            if not self._esperar_captcha(pg):
                raise ErrorEnvio("No se resolvió el captcha a tiempo.")

        def confirmar(resumen: str) -> bool:
            self._respuesta.clear()
            self.pedir_confirmacion.emit(pid, resumen)
            self._respuesta.wait()
            return self._aceptado

        flujo = FlujoOferta(page, oferta, documentos, modo, credenciales,
                            lambda m: self.estado.emit(f"[{pid}] {m}"), esperar, confirmar)
        try:
            estado, detalle = flujo.ejecutar()
        except ErrorEnvio as e:
            estado, detalle = "error", str(e)
        except Exception as e:  # errores de Playwright, tiempos de espera…
            flujo._captura("error")
            estado, detalle = "error", f"{e.__class__.__name__}: {str(e).splitlines()[0][:300]}"
        self.estado.emit(f"[{pid}] {detalle}")
        self.oferta_resultado.emit(pid, estado, detalle)

    def _descargar_documentos(self, page, pid: str) -> None:
        destino = DOWNLOAD_DIR / _nombre_seguro(pid)
        destino.mkdir(parents=True, exist_ok=True)
        enlaces = page.locator("a, input[type=button], button").filter(has_text=PATRON_DESCARGA)
        extra = page.locator("a[href*='Download' i], a[id*='Download' i], a[onclick*='Download' i]")
        candidatos = [enlaces.nth(i) for i in range(enlaces.count())]
        candidatos += [extra.nth(i) for i in range(extra.count())]

        guardados: list[str] = []
        vistos: set[str] = set()
        for el in candidatos:
            try:
                clave = (el.get_attribute("id") or "") + (el.get_attribute("href") or "") + el.inner_text()
            except Exception:
                continue
            if clave in vistos or not el.is_visible():
                continue
            vistos.add(clave)
            try:
                with page.expect_download(timeout=30_000) as info:
                    el.click()
                d = info.value
                ruta = _ruta_libre(destino / _nombre_seguro(d.suggested_filename))
                d.save_as(str(ruta))
                guardados.append(ruta.name)
                self.estado.emit(f"Descargado: {ruta.name}")
            except Exception:
                continue
            if _hay_captcha(page):
                self.estado.emit("SECOP volvió a pedir captcha; resuélvelo y vuelve a descargar.")
                break

        if guardados:
            self.estado.emit(f"{len(guardados)} documento(s) guardados en {destino}")
        else:
            self.estado.emit("No se encontraron documentos descargables en la página.")
        self.descargas.emit(str(destino), guardados)


def _ruta_libre(ruta: Path) -> Path:
    if not ruta.exists():
        return ruta
    n = 1
    while (nueva := ruta.with_name(f"{ruta.stem}_{n}{ruta.suffix}")).exists():
        n += 1
    return nueva
