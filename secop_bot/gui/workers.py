"""Ejecución en segundo plano para no bloquear la interfaz."""

import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Senales(QObject):
    terminado = Signal(object)
    error = Signal(str)
    progreso = Signal(object)


class Tarea(QRunnable):
    """Ejecuta fn(*args, progreso=callback, **kwargs) en el pool de hilos.

    Si fn no acepta `progreso`, pásale con_progreso=False.
    """

    def __init__(self, fn, *args, con_progreso: bool = False, **kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.senales = _Senales()
        if con_progreso:
            self.kwargs["progreso"] = self.senales.progreso.emit

    def run(self) -> None:
        try:
            resultado = self.fn(*self.args, **self.kwargs)
        except Exception as e:
            traceback.print_exc()
            self.senales.error.emit(str(e) or e.__class__.__name__)
        else:
            self.senales.terminado.emit(resultado)


# Mantiene vivas las tareas (y sus señales) hasta que terminan; si no, el recolector
# de Python puede destruirlas antes de que lleguen las señales encoladas.
_activas: set[Tarea] = set()


def ejecutar(tarea: Tarea, al_terminar=None, al_error=None, al_progreso=None) -> Tarea:
    _activas.add(tarea)
    tarea.setAutoDelete(False)
    if al_terminar:
        tarea.senales.terminado.connect(al_terminar)
    if al_error:
        tarea.senales.error.connect(al_error)
    if al_progreso:
        tarea.senales.progreso.connect(al_progreso)
    tarea.senales.terminado.connect(lambda _r: _activas.discard(tarea))
    tarea.senales.error.connect(lambda _e: _activas.discard(tarea))
    QThreadPool.globalInstance().start(tarea)
    return tarea
