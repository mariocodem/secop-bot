"""Pestaña «Ofertas»: cuenta SECOP II, modo de envío, reglas automáticas y cola de ofertas."""

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import config, ofertas
from ..envio import SELECTORES_FILE, cargar_selectores
from ..models import Proceso
from ..ofertas import ColaOfertas, Oferta, Reglas
from .results_table import pesos

COLUMNAS = ["Proceso", "Nombre", "Entidad", "Valor ofertado", "Cierre", "Origen", "Estado", "Detalle"]


class _Avisos(QObject):
    log = Signal(str)
    notificar = Signal(str, str)


class PestanaOfertas(QWidget):
    def __init__(self, ajustes: dict, navegador, parent=None):
        super().__init__(parent)
        self.ajustes = ajustes
        self.navegador = navegador
        self.cola = ColaOfertas(config.DB_FILE)
        self.avisos = _Avisos()
        self.log = self.avisos.log
        self.notificar = self.avisos.notificar
        navegador.oferta_resultado.connect(self._resultado)
        navegador.pedir_confirmacion.connect(self._confirmar)

        cfg = ajustes.setdefault("ofertas", {})
        reglas = Reglas.desde(cfg.get("reglas"))
        lay = QVBoxLayout(self)

        fila = QHBoxLayout()
        # --- cuenta y modo
        g = QGroupBox("Cuenta de proveedor SECOP II y modo de envío")
        f = QFormLayout(g)
        self.usuario = QLineEdit(cfg.get("usuario", ""))
        self.clave = QLineEdit(config.leer_secreto(f"secop:{cfg.get('usuario', '')}") if cfg.get("usuario") else "")
        self.clave.setEchoMode(QLineEdit.Password)
        self.clave.setPlaceholderText("Opcional: si la dejas vacía inicias sesión tú en el navegador")
        self.modo = QComboBox()
        for clave, texto in ofertas.MODOS.items():
            self.modo.addItem(texto, clave)
        self.modo.setCurrentIndex(max(0, self.modo.findData(cfg.get("modo", "simulacion"))))
        self.modo.currentIndexChanged.connect(self._modo_cambiado)
        f.addRow("Usuario:", self.usuario)
        f.addRow("Contraseña:", self.clave)
        f.addRow("Modo:", self.modo)
        f.addRow(QLabel("La contraseña se guarda en el llavero del sistema. Si SECOP pide captcha, lo resuelves tú."))
        fila.addWidget(g, 1)

        # --- reglas
        g = QGroupBox("Reglas de oferta automática (para convocatorias nuevas detectadas por el monitor)")
        f = QFormLayout(g)
        self.r_activo = QCheckBox("Crear y enviar ofertas automáticamente cuando un proceso cumpla todas las reglas")
        self.r_activo.setChecked(reglas.activo)
        self.r_modalidades = QLineEdit(", ".join(reglas.modalidades))
        self.r_precio = QDoubleSpinBox()
        self.r_precio.setRange(0, 1e13)
        self.r_precio.setDecimals(0)
        self.r_precio.setGroupSeparatorShown(True)
        self.r_precio.setPrefix("$ ")
        self.r_precio.setValue(reglas.precio_max)
        self.r_puntaje = QSpinBox()
        self.r_puntaje.setRange(0, 50)
        self.r_puntaje.setValue(reglas.puntaje_min)
        self.r_porcentaje = QDoubleSpinBox()
        self.r_porcentaje.setRange(1, 100)
        self.r_porcentaje.setSuffix(" % del precio base")
        self.r_porcentaje.setValue(reglas.porcentaje)
        self.r_max_dia = QSpinBox()
        self.r_max_dia.setRange(1, 100)
        self.r_max_dia.setValue(reglas.max_por_dia)
        self.r_carpeta = QCheckBox("Solo si existe la carpeta del proceso con documentos propios")
        self.r_carpeta.setChecked(reglas.requiere_carpeta)
        f.addRow("", self.r_activo)
        f.addRow("Modalidades:", self.r_modalidades)
        f.addRow("Precio base máximo:", self.r_precio)
        f.addRow("Puntaje tecnología mín.:", self.r_puntaje)
        f.addRow("Valor a ofertar:", self.r_porcentaje)
        f.addRow("Máximo de ofertas/día:", self.r_max_dia)
        f.addRow("", self.r_carpeta)
        fila.addWidget(g, 1)
        lay.addLayout(fila)

        bl = QHBoxLayout()
        for texto, fn in (
            ("Guardar configuración", self.guardar),
            ("Abrir biblioteca de documentos", lambda: self._abrir(ofertas.BIBLIOTECA_DIR)),
            ("Abrir carpeta de ofertas", lambda: self._abrir(ofertas.OFERTAS_DIR)),
            ("Abrir evidencias", lambda: self._abrir(ofertas.EVIDENCIAS_DIR)),
            ("Editar selectores", self._editar_selectores),
        ):
            b = QPushButton(texto)
            b.clicked.connect(fn)
            bl.addWidget(b)
        bl.addStretch()
        lay.addLayout(bl)

        # --- cola
        lay.addWidget(QLabel("Cola de ofertas:"))
        self.tabla = QTableWidget(0, len(COLUMNAS))
        self.tabla.setHorizontalHeaderLabels(COLUMNAS)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tabla.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tabla.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tabla.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.tabla.horizontalHeader().setStretchLastSection(True)
        for col, ancho in enumerate([140, 320, 200, 130, 90, 70, 90]):
            self.tabla.setColumnWidth(col, ancho)
        lay.addWidget(self.tabla, 1)

        bl = QHBoxLayout()
        for texto, fn in (
            ("Ejecutar oferta seleccionada", self._ejecutar_sel),
            ("Cambiar valor…", self._cambiar_valor),
            ("Abrir carpeta del proceso", lambda: (o := self._sel()) and self._abrir(o.carpeta)),
            ("Ver documentos", self._ver_documentos),
            ("Quitar de la cola", self._quitar),
        ):
            b = QPushButton(texto)
            b.clicked.connect(fn)
            bl.addWidget(b)
        bl.addStretch()
        lay.addLayout(bl)

        cargar_selectores()  # crea selectores.json la primera vez
        self.refrescar()

    # ---------------------------------------------------------------- config
    def reglas(self) -> Reglas:
        return Reglas(
            activo=self.r_activo.isChecked(),
            modalidades=[m.strip() for m in self.r_modalidades.text().split(",") if m.strip()],
            precio_max=self.r_precio.value(),
            puntaje_min=self.r_puntaje.value(),
            porcentaje=self.r_porcentaje.value(),
            max_por_dia=self.r_max_dia.value(),
            requiere_carpeta=self.r_carpeta.isChecked(),
        )

    def guardar(self) -> None:
        usuario = self.usuario.text().strip()
        self.ajustes["ofertas"] = {
            "usuario": usuario,
            "modo": self.modo.currentData(),
            "reglas": self.reglas().__dict__,
        }
        if usuario and self.clave.text() and not config.guardar_secreto(f"secop:{usuario}", self.clave.text()):
            QMessageBox.warning(self, "Llavero", "No hay llavero del sistema: la contraseña de SECOP no se guardará.")
        config.guardar(self.ajustes)
        self.log.emit("Configuración de ofertas guardada.")

    def _modo_cambiado(self) -> None:
        if self.modo.currentData() == "automatico":
            r = QMessageBox.warning(
                self, "Modo automático",
                "En modo automático el bot PRESENTA las ofertas en SECOP II sin pedirte confirmación.\n"
                "Una oferta presentada es vinculante para tu empresa.\n\n"
                "Recomendación: prueba primero en Simulación y revisa las evidencias.\n\n¿Activar el modo automático?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if r != QMessageBox.Yes:
                self.modo.setCurrentIndex(self.modo.findData("simulacion"))

    def _credenciales(self) -> tuple[str, str]:
        usuario = self.usuario.text().strip()
        clave = self.clave.text() or (config.leer_secreto(f"secop:{usuario}") if usuario else "")
        return usuario, clave

    # ------------------------------------------------------------------ cola
    def refrescar(self) -> None:
        lista = self.cola.todas()
        self.tabla.setRowCount(len(lista))
        for fila, o in enumerate(lista):
            valores = [o.proceso_id, o.nombre, o.entidad, pesos(o.valor_oferta), o.fecha_cierre,
                       o.origen, o.estado, o.detalle]
            for col, v in enumerate(valores):
                item = QTableWidgetItem(str(v or ""))
                item.setData(Qt.UserRole, o.proceso_id)
                if col == 1:
                    item.setToolTip(o.nombre)
                if col == 7:
                    item.setToolTip(o.detalle)
                self.tabla.setItem(fila, col, item)

    def _sel(self) -> Oferta | None:
        fila = self.tabla.currentRow()
        if fila < 0:
            return None
        return self.cola.obtener(self.tabla.item(fila, 0).data(Qt.UserRole))

    def preparar(self, p: Proceso | None) -> None:
        """Desde la pestaña Buscar: crea la oferta con el valor que indique el usuario."""
        if not p:
            return
        if not p.url:
            QMessageBox.warning(self, "Sin enlace", "Este proceso no tiene URL de SECOP II.")
            return
        sugerido = round(p.precio_base * self.r_porcentaje.value() / 100) if p.precio_base else 0
        valor, ok = QInputDialog.getDouble(
            self, "Preparar oferta",
            f"{p.nombre[:120]}\nPrecio base: {pesos(p.precio_base)}\n\nValor a ofertar (COP):",
            sugerido, 1, 1e13, 0,
        )
        if not ok:
            return
        o = ofertas.nueva_oferta(p, valor, "manual")
        if not self.cola.agregar(o):
            QMessageBox.information(self, "Ya existe", "Ya hay una oferta en la cola para este proceso.")
            return
        o.carpeta.mkdir(parents=True, exist_ok=True)
        self.refrescar()
        self.log.emit(f"Oferta preparada para {p.id}. Pon sus documentos propios en {o.carpeta}")
        self._abrir(o.carpeta)

    def _ejecutar_sel(self) -> None:
        o = self._sel()
        if not o:
            return
        if o.estado == "enviada":
            QMessageBox.information(self, "Ya enviada", "Esta oferta ya fue presentada.")
            return
        self.ejecutar(o)

    def ejecutar(self, o: Oferta) -> None:
        docs = ofertas.documentos_de(o)
        if not docs:
            QMessageBox.warning(self, "Sin documentos",
                                f"Agrega documentos a la biblioteca ({ofertas.BIBLIOTECA_DIR}) o a {o.carpeta}.")
            return
        modo = self.modo.currentData()
        self.cola.actualizar(o.proceso_id, "en_proceso", f"Modo {modo}…")
        self.refrescar()
        self.navegador.ofertar(o, docs, modo, self._credenciales())

    def _cambiar_valor(self) -> None:
        o = self._sel()
        if not o or o.estado == "enviada":
            return
        valor, ok = QInputDialog.getDouble(self, "Valor de la oferta", "Valor (COP):", o.valor_oferta, 1, 1e13, 0)
        if ok:
            self.cola.cambiar_valor(o.proceso_id, valor)
            self.refrescar()

    def _ver_documentos(self) -> None:
        o = self._sel()
        if not o:
            return
        docs = ofertas.documentos_de(o)
        texto = "\n".join(f"• {d.parent.name}/{d.name}" for d in docs) or "(ninguno)"
        QMessageBox.information(self, "Documentos que se anexarán", texto)

    def _quitar(self) -> None:
        o = self._sel()
        if o and o.estado != "en_proceso":
            self.cola.borrar(o.proceso_id)
            self.refrescar()

    # ---------------------------------------------------------- automático
    def evaluar_nuevos(self, procesos: list[Proceso]) -> None:
        """Aplica las reglas a las convocatorias nuevas y lanza las que cumplan."""
        reglas = self.reglas()
        if not reglas.activo:
            return
        for p in procesos:
            ok, motivo = ofertas.evaluar_reglas(p, reglas, self.cola.automaticas_hoy())
            if not ok:
                continue
            valor = round(p.precio_base * reglas.porcentaje / 100)
            o = ofertas.nueva_oferta(p, valor, "regla")
            if not self.cola.agregar(o):
                continue
            self.log.emit(f"[ofertas] {p.id} cumple las reglas ({pesos(valor)}): iniciando envío.")
            self.notificar.emit("Oferta automática", f"Iniciando oferta para {p.nombre[:80]}")
            self.ejecutar(o)
        self.refrescar()

    # ------------------------------------------------------------ navegador
    def _resultado(self, pid: str, estado: str, detalle: str) -> None:
        self.cola.actualizar(pid, estado, detalle)
        self.refrescar()
        self.log.emit(f"[ofertas] {pid}: {estado} – {detalle}")
        titulo = {"enviada": "✅ Oferta presentada", "lista": "Oferta lista (sin presentar)"}.get(estado, "⚠ Oferta con error")
        self.notificar.emit(titulo, f"{pid}: {detalle[:150]}")

    def _confirmar(self, pid: str, resumen: str) -> None:
        r = QMessageBox.question(
            self, "¿Presentar la oferta?",
            f"{resumen}\n\nEl formulario está completo en el navegador. ¿Presentar la oferta en SECOP II?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        self.navegador.responder(r == QMessageBox.Yes)

    # -------------------------------------------------------------- varios
    @staticmethod
    def _abrir(ruta) -> None:
        ruta.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ruta)))

    def _editar_selectores(self) -> None:
        cargar_selectores()
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(SELECTORES_FILE)))
