"""Tabla de resultados de procesos."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QMenu, QTableView

from ..models import Proceso

COLUMNAS = [
    ("Publicación", "fecha_publicacion"),
    ("Cierre", "fecha_cierre"),
    ("Días", "dias_restantes"),
    ("Alerta", "etiqueta"),
    ("Tec", "puntaje_tec"),
    ("Proceso", "nombre"),
    ("Entidad", "entidad"),
    ("Departamento", "departamento"),
    ("Modalidad", "modalidad"),
    ("Precio base", "precio_base"),
    ("Fase", "fase"),
    ("Referencia", "referencia"),
]


def pesos(valor: float) -> str:
    return "$ " + f"{valor:,.0f}".replace(",", ".")


class ModeloProcesos(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.procesos: list[Proceso] = []
        self.resaltados: set[str] = set()

    def poner(self, procesos: list[Proceso], resaltados: set[str] | None = None) -> None:
        self.beginResetModel()
        self.procesos = procesos
        self.resaltados = resaltados or set()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.procesos)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNAS)

    def headerData(self, seccion, orientacion, rol=Qt.DisplayRole):
        if orientacion == Qt.Horizontal and rol == Qt.DisplayRole:
            return COLUMNAS[seccion][0]
        return None

    def data(self, idx, rol=Qt.DisplayRole):
        p = self.procesos[idx.row()]
        attr = COLUMNAS[idx.column()][1]
        valor = getattr(p, attr)
        if rol == Qt.DisplayRole:
            if attr == "precio_base":
                return pesos(valor)
            if attr == "dias_restantes":
                return "" if valor is None else ("HOY" if valor == 0 else str(valor))
            return valor
        if rol == Qt.UserRole:  # para ordenar
            return 9999 if valor is None else valor
        if rol == Qt.BackgroundRole and attr in ("dias_restantes", "fecha_cierre") and valor is not None:
            dias = p.dias_restantes
            if dias is not None and dias <= 1:
                return QBrush(QColor(255, 120, 120, 90))
            if dias is not None and dias <= 3:
                return QBrush(QColor(255, 200, 80, 90))
        if rol == Qt.ToolTipRole and attr == "puntaje_tec":
            return ", ".join(p.terminos_tec)
        if rol == Qt.ToolTipRole and attr == "nombre":
            return p.descripcion
        if rol == Qt.TextAlignmentRole and attr == "precio_base":
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if rol == Qt.FontRole and p.id in self.resaltados:
            f = QFont()
            f.setBold(True)
            return f
        return None


class TablaProcesos(QTableView):
    abrir = Signal(object, bool)  # proceso, descargar documentos
    seleccionado = Signal(object)
    preparar_oferta = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.modelo = ModeloProcesos(self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.modelo)
        self.proxy.setSortRole(Qt.UserRole)
        self.setModel(self.proxy)
        self.setSortingEnabled(True)
        self.sortByColumn(0, Qt.DescendingOrder)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.verticalHeader().hide()
        h = self.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setStretchLastSection(False)
        for col, ancho in enumerate([95, 95, 50, 150, 40, 400, 240, 120, 170, 130, 150, 130]):
            self.setColumnWidth(col, ancho)
        self.doubleClicked.connect(lambda idx: self.abrir.emit(self._proceso(idx), False))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.selectionModel().currentRowChanged.connect(lambda idx, _: self.seleccionado.emit(self._proceso(idx)))

    def poner(self, procesos: list[Proceso], resaltados: set[str] | None = None) -> None:
        self.modelo.poner(procesos, resaltados)

    def _proceso(self, idx_proxy) -> Proceso | None:
        if not idx_proxy.isValid():
            return None
        return self.modelo.procesos[self.proxy.mapToSource(idx_proxy).row()]

    def actual(self) -> Proceso | None:
        return self._proceso(self.currentIndex())

    def _menu(self, pos) -> None:
        p = self._proceso(self.indexAt(pos))
        if not p:
            return
        m = QMenu(self)
        m.addAction("Abrir en SECOP (navegador asistido)", lambda: self.abrir.emit(p, False))
        m.addAction("Abrir y descargar documentos", lambda: self.abrir.emit(p, True))
        m.addAction("Preparar oferta…", lambda: self.preparar_oferta.emit(p))
        m.addSeparator()
        m.addAction("Abrir en el navegador del sistema", lambda: QDesktopServices.openUrl(QUrl(p.url)))
        m.addAction("Copiar enlace", lambda: QGuiApplication.clipboard().setText(p.url))
        m.exec(self.viewport().mapToGlobal(pos))
