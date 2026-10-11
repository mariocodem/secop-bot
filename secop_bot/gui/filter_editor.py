"""Editor de filtros dinámico: palabras clave + filas [Campo][Operador][Valor][✕]."""

from datetime import date, timedelta

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import api
from ..models import Busqueda, Filtro
from .workers import Tarea, ejecutar

# Valores posibles por campo, compartidos entre filas y cargados una sola vez.
_cache_valores: dict[str, list[str]] = {}


class FilaFiltro(QWidget):
    quitar = Signal(object)

    def __init__(self, app_token: str = "", parent=None):
        super().__init__(parent)
        self.app_token = app_token
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        self.campo = QComboBox()
        for clave, (etiqueta, _) in api.CAMPOS.items():
            self.campo.addItem(etiqueta, clave)
        self.operador = QComboBox()
        self.valor = QComboBox()
        self.valor.setEditable(True)
        self.valor.setInsertPolicy(QComboBox.NoInsert)
        self.valor.setMinimumWidth(220)
        self.valor2 = QLineEdit()
        self.valor2.setPlaceholderText("y…")
        btn_quitar = QToolButton()
        btn_quitar.setText("✕")
        btn_quitar.setToolTip("Quitar filtro")
        btn_quitar.clicked.connect(lambda: self.quitar.emit(self))

        lay.addWidget(self.campo, 2)
        lay.addWidget(self.operador, 1)
        lay.addWidget(self.valor, 3)
        lay.addWidget(self.valor2, 1)
        lay.addWidget(btn_quitar)

        self.campo.currentIndexChanged.connect(self._campo_cambiado)
        self.operador.currentTextChanged.connect(self._operador_cambiado)
        self._campo_cambiado()

    @property
    def tipo(self) -> str:
        return api.CAMPOS[self.campo.currentData()][1]

    def _campo_cambiado(self) -> None:
        clave = self.campo.currentData()
        self.operador.blockSignals(True)
        self.operador.clear()
        self.operador.addItems(api.OPERADORES[self.tipo])
        self.operador.blockSignals(False)

        self.valor.clear()
        self.valor.setEditText("")
        if clave in api.CAMPOS_CON_LISTA:
            self.operador.setCurrentText("igual a")
            self._cargar_valores(clave)
        self._operador_cambiado(self.operador.currentText())

    def _operador_cambiado(self, op: str) -> None:
        self.valor2.setVisible(op == "entre")
        placeholders = {
            "numero": "Ej: 50000000",
            "texto": "Texto…",
        }
        if self.tipo == "fecha":
            if op == "últimos N días":
                ph, defecto = "Número de días", "7"
            else:
                ph, defecto = "AAAA-MM-DD", (date.today() - timedelta(days=30)).isoformat()
            self.valor.setEditText(defecto)
        else:
            ph = placeholders[self.tipo]
        self.valor.lineEdit().setPlaceholderText(ph)

    def _cargar_valores(self, clave: str) -> None:
        if clave in _cache_valores:
            self._poner_valores(clave, _cache_valores[clave])
            return
        self.valor.lineEdit().setPlaceholderText("Cargando valores…")

        def listo(valores, clave=clave):
            _cache_valores[clave] = valores
            self._poner_valores(clave, valores)

        ejecutar(Tarea(api.valores_distintos, clave, self.app_token), listo, lambda _e: None)

    def _poner_valores(self, clave: str, valores: list[str]) -> None:
        try:
            if self.campo.currentData() != clave:
                return
        except RuntimeError:  # la fila se quitó mientras cargaban los valores
            return
        texto = self.valor.currentText()
        self.valor.clear()
        self.valor.addItems(valores)
        self.valor.setEditText(texto)
        self.valor.lineEdit().setPlaceholderText("Elige o escribe…")

    def filtro(self) -> Filtro:
        return Filtro(self.campo.currentData(), self.operador.currentText(), self.valor.currentText(), self.valor2.text())

    def cargar(self, f: Filtro) -> None:
        idx = self.campo.findData(f.campo)
        if idx >= 0:
            self.campo.setCurrentIndex(idx)
        self.operador.setCurrentText(f.operador)
        self.valor.setEditText(f.valor)
        self.valor2.setText(f.valor2)


class EditorFiltros(QWidget):
    buscar = Signal()

    def __init__(self, app_token: str = "", parent=None):
        super().__init__(parent)
        self.app_token = app_token
        self.filas: list[FilaFiltro] = []

        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(0, 0, 0, 0)

        form = QFormLayout()
        self.palabras = QLineEdit()
        self.palabras.setPlaceholderText("Opcional: términos extra separados por coma (basta con que coincida uno)")
        self.palabras.returnPressed.connect(self.buscar)
        form.addRow("Palabras clave:", self.palabras)
        self.solo_abiertos = QCheckBox("Solo convocatorias vigentes (publicadas y con fecha de cierre de hoy en adelante)")
        self.solo_abiertos.setChecked(True)
        form.addRow("", self.solo_abiertos)
        self.solo_tecnologia = QCheckBox("Solo tecnología (software, hardware, redes, nube, ciberseguridad, TIC…)")
        self.solo_tecnologia.setChecked(True)
        form.addRow("", self.solo_tecnologia)
        raiz.addLayout(form)

        self.contenedor = QVBoxLayout()
        raiz.addLayout(self.contenedor)

        btn = QPushButton("+ Agregar filtro")
        btn.clicked.connect(lambda: self.agregar_fila())
        fila_btn = QHBoxLayout()
        fila_btn.addWidget(btn)
        fila_btn.addStretch()
        raiz.addLayout(fila_btn)

    def agregar_fila(self, f: Filtro | None = None) -> FilaFiltro:
        fila = FilaFiltro(self.app_token)
        fila.quitar.connect(self._quitar)
        if f:
            fila.cargar(f)
        self.filas.append(fila)
        self.contenedor.addWidget(fila)
        return fila

    def _quitar(self, fila: FilaFiltro) -> None:
        self.filas.remove(fila)
        fila.setParent(None)
        fila.deleteLater()

    def busqueda(self) -> Busqueda:
        return Busqueda(
            palabras_clave=self.palabras.text(),
            solo_abiertos=self.solo_abiertos.isChecked(),
            solo_tecnologia=self.solo_tecnologia.isChecked(),
            filtros=[f.filtro() for f in self.filas],
        )

    def cargar(self, b: Busqueda) -> None:
        for fila in list(self.filas):
            self._quitar(fila)
        self.palabras.setText(b.palabras_clave)
        self.solo_abiertos.setChecked(b.solo_abiertos)
        self.solo_tecnologia.setChecked(b.solo_tecnologia)
        for f in b.filtros:
            self.agregar_fila(f)
