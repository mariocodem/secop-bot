"""Monitor de perfiles activos.

datos.gov.co recarga el dataset de SECOP II una vez al día. Para enterarse lo antes
posible, el monitor consulta cada pocos minutos la marca `rowsUpdatedAt` (una petición
muy liviana) y solo cuando cambia ejecuta la búsqueda completa de los perfiles.
"""

from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

from . import api, config, notifier
from .gui.workers import Tarea, ejecutar
from .storage import Storage


class Programador(QObject):
    log = Signal(str)
    nuevos = Signal(str, list)  # nombre de perfil, procesos
    corriendo = Signal(bool)

    def __init__(self, storage: Storage, parent=None):
        super().__init__(parent)
        self.storage = storage
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self._ocupado = False

    def iniciar(self, intervalo_minutos: int) -> None:
        self.timer.start(max(1, intervalo_minutos) * 60 * 1000)
        self.log.emit(f"Monitor iniciado: comprueba datos nuevos cada {intervalo_minutos} min.")
        self.corriendo.emit(True)
        self._tick()

    def detener(self) -> None:
        self.timer.stop()
        self.log.emit("Búsqueda automática detenida.")
        self.corriendo.emit(False)

    @property
    def activo(self) -> bool:
        return self.timer.isActive()

    def _tick(self) -> None:
        self.ejecutar_ahora(forzar=False)

    def ejecutar_ahora(self, forzar: bool = True) -> None:
        if self._ocupado:
            self.log.emit("Ya hay una revisión en curso; se omite este ciclo.")
            return
        self._ocupado = True
        tarea = Tarea(self._ciclo, forzar, con_progreso=True)
        ejecutar(tarea, self._fin, self._error, lambda msg: self.log.emit(msg))

    def _ciclo(self, forzar: bool, progreso) -> list[tuple[str, list]]:
        ajustes = config.cargar()
        marca = api.ultima_actualizacion(ajustes["app_token"])
        if not forzar and marca == ajustes.get("ultima_carga"):
            return []
        cuando = datetime.fromtimestamp(marca).strftime("%Y-%m-%d %H:%M")
        progreso(f"Datos de SECOP II cargados en datos.gov.co el {cuando}: revisando perfiles…")
        smtp = config.smtp_desde(ajustes)
        perfiles = [p for p in self.storage.perfiles() if p.activo]
        if not perfiles:
            progreso("No hay perfiles activos. Activa al menos uno en la pestaña Perfiles.")
            return []
        resultados = []
        hubo_error = False
        for perfil in perfiles:
            try:
                procesos = api.buscar(perfil.busqueda, ajustes["max_resultados"], ajustes["app_token"])
            except Exception as e:
                progreso(f"[{perfil.nombre}] error en la búsqueda: {e}")
                hubo_error = True
                continue
            primera_vez = not perfil.inicializado
            nuevos = self.storage.registrar_nuevos(perfil, procesos)
            if primera_vez:
                progreso(f"[{perfil.nombre}] primera revisión: {len(procesos)} procesos registrados (no se notifican).")
                continue
            progreso(f"[{perfil.nombre}] {len(procesos)} procesos, {len(nuevos)} nuevos.")
            if nuevos:
                nuevos.sort(key=prioridad)
                resultados.append((perfil.nombre, nuevos))
                if smtp.destinatarios:
                    try:
                        notifier.enviar_nuevos(smtp, perfil.nombre, nuevos)
                        progreso(f"[{perfil.nombre}] correo enviado a {smtp.destinatarios}.")
                    except Exception as e:
                        progreso(f"[{perfil.nombre}] no se pudo enviar el correo: {e}")
        if not hubo_error:  # si algo falló, se reintenta en el siguiente tick
            ajustes = config.cargar()
            ajustes["ultima_carga"] = marca
            config.guardar(ajustes)
        return resultados

    def _fin(self, resultados) -> None:
        self._ocupado = False
        for nombre, procesos in resultados:
            self.nuevos.emit(nombre, procesos)

    def _error(self, msg: str) -> None:
        self._ocupado = False
        self.log.emit(f"Error en la revisión: {msg}")


def prioridad(p) -> tuple:
    """Primero mínima cuantía, luego lo que cierra antes y lo más claramente tecnológico."""
    dias = p.dias_restantes if p.dias_restantes is not None else 999
    return (not p.es_rapida, dias, -p.puntaje_tec)
