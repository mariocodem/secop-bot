"""Ventana principal: Buscar | Perfiles | Automático | Ofertas | Ajustes."""

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMenu
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStyle,
    QSystemTrayIcon,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import api, config, notifier
from ..browser import NavegadorAsistido
from ..correo_entrante import VigilanteCorreo, imap_desde
from ..models import Busqueda, Proceso
from ..scheduler import Programador
from ..storage import Storage
from .filter_editor import EditorFiltros
from .ofertas_tab import PestanaOfertas
from .results_table import TablaProcesos, pesos
from .workers import Tarea, ejecutar


class VentanaPrincipal(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bot de licitaciones SECOP II")
        self.resize(1300, 820)

        self.ajustes = config.cargar()
        self.storage = Storage(config.DB_FILE)
        self.storage.crear_perfil_inicial()
        self.navegador = NavegadorAsistido(self)
        self.navegador.estado.connect(self._estado)
        self.navegador.captcha.connect(self._captcha)
        self.programador = Programador(self.storage, self)
        self.programador.log.connect(self._log)
        self.programador.nuevos.connect(self._nuevos)
        self.programador.corriendo.connect(self._auto_corriendo)
        self._nuevos_acumulados: list[Proceso] = []
        self.vigilante = VigilanteCorreo(self)
        self.vigilante.log.connect(self._log)
        self.vigilante.nuevos.connect(self._nuevos)
        self.ofertas = PestanaOfertas(self.ajustes, self.navegador, self)
        self.ofertas.log.connect(self._log)
        self.ofertas.notificar.connect(self._notificar)

        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(self.style().standardIcon(QStyle.SP_FileDialogContentsView), self)
            self.tray.setToolTip("Bot SECOP II")
            self.tray.activated.connect(lambda _: (self.showNormal(), self.activateWindow()))
            menu = QMenu(self)
            menu.addAction("Mostrar", lambda: (self.showNormal(), self.activateWindow()))
            menu.addAction("Salir", self._salir)
            self.tray.setContextMenu(menu)
            self.tray.show()

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_buscar(), "Buscar")
        self.tabs.addTab(self._tab_perfiles(), "Perfiles")
        self.tabs.addTab(self._tab_auto(), "Automático")
        self.tabs.addTab(self.ofertas, "Ofertas")
        self.tabs.addTab(self._tab_ajustes(), "Ajustes y correo")
        self.tabs.currentChanged.connect(lambda i: i == 1 and self._refrescar_perfiles())
        self.setCentralWidget(self.tabs)
        self.statusBar().showMessage("Listo. Datos: datos.gov.co – SECOP II Procesos de Contratación")
        if self.ajustes.get("auto_iniciar", True):
            self._alternar_auto()
        if imap_desde(self.ajustes).activo:
            self.vigilante.iniciar()
        QTimer.singleShot(0, self._buscar)  # muestra de entrada las convocatorias vigentes de tecnología

    # ------------------------------------------------------------------ Buscar
    def _tab_buscar(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        grupo = QGroupBox("Filtros")
        gl = QVBoxLayout(grupo)
        self.editor = EditorFiltros(self.ajustes.get("app_token", ""))
        self.editor.buscar.connect(self._buscar)
        gl.addWidget(self.editor)
        botones = QHBoxLayout()
        self.btn_buscar = QPushButton("Buscar")
        self.btn_buscar.setDefault(True)
        self.btn_buscar.clicked.connect(self._buscar)
        btn_guardar = QPushButton("Guardar como perfil…")
        btn_guardar.clicked.connect(self._guardar_perfil)
        btn_limpiar = QPushButton("Limpiar")
        btn_limpiar.clicked.connect(lambda: self.editor.cargar(Busqueda()))
        botones.addWidget(self.btn_buscar)
        botones.addWidget(btn_guardar)
        botones.addWidget(btn_limpiar)
        botones.addStretch()
        self.lbl_total = QLabel("")
        botones.addWidget(self.lbl_total)
        gl.addLayout(botones)
        lay.addWidget(grupo)

        split = QSplitter(Qt.Vertical)
        self.tabla = TablaProcesos()
        self.tabla.abrir.connect(self._abrir_proceso)
        self.tabla.seleccionado.connect(self._mostrar_detalle)
        self.tabla.preparar_oferta.connect(self._preparar_oferta)
        split.addWidget(self.tabla)

        detalle = QWidget()
        dl = QVBoxLayout(detalle)
        dl.setContentsMargins(0, 0, 0, 0)
        self.detalle = QTextBrowser()
        self.detalle.setOpenExternalLinks(True)
        dl.addWidget(self.detalle)
        acc = QHBoxLayout()
        b1 = QPushButton("Abrir en SECOP (asistido)")
        b1.clicked.connect(lambda: self._abrir_proceso(self.tabla.actual(), False))
        b2 = QPushButton("Abrir y descargar documentos")
        b2.clicked.connect(lambda: self._abrir_proceso(self.tabla.actual(), True))
        b3 = QPushButton("Abrir en navegador del sistema")
        b3.clicked.connect(lambda: (p := self.tabla.actual()) and QDesktopServices.openUrl(QUrl(p.url)))
        b4 = QPushButton("Preparar oferta…")
        b4.clicked.connect(lambda: self._preparar_oferta(self.tabla.actual()))
        for b in (b1, b2, b3, b4):
            acc.addWidget(b)
        acc.addStretch()
        dl.addLayout(acc)
        split.addWidget(detalle)
        split.setSizes([520, 180])
        lay.addWidget(split, 1)
        return w

    def _buscar(self) -> None:
        try:
            busqueda = self.editor.busqueda()
            api.construir_where(busqueda)  # valida antes de lanzar
        except (ValueError, KeyError) as e:
            QMessageBox.warning(self, "Filtro inválido", str(e))
            return
        self.btn_buscar.setEnabled(False)
        self.lbl_total.setText("Buscando…")
        ejecutar(
            Tarea(
                api.buscar,
                busqueda,
                self.ajustes["max_resultados"],
                self.ajustes.get("app_token", ""),
                con_progreso=True,
            ),
            self._resultados,
            self._error_busqueda,
            lambda n: self.lbl_total.setText(f"Buscando… {n} resultados"),
        )

    def _resultados(self, procesos: list[Proceso]) -> None:
        self.btn_buscar.setEnabled(True)
        self.tabla.poner(procesos)
        extra = " (límite alcanzado; ajústalo en Ajustes)" if len(procesos) >= self.ajustes["max_resultados"] else ""
        self.lbl_total.setText(f"{len(procesos)} procesos{extra}")

    def _error_busqueda(self, msg: str) -> None:
        self.btn_buscar.setEnabled(True)
        self.lbl_total.setText("")
        QMessageBox.critical(self, "Error en la búsqueda", msg)

    def _mostrar_detalle(self, p: Proceso | None) -> None:
        if not p:
            self.detalle.clear()
            return
        self.detalle.setHtml(
            f"<b>{p.nombre}</b><br>"
            f"{p.entidad} · {p.departamento} / {p.ciudad}<br>"
            f"<b>Modalidad:</b> {p.modalidad} · <b>Tipo:</b> {p.tipo_contrato} · "
            f"<b>Precio base:</b> {pesos(p.precio_base)} · <b>Fase:</b> {p.fase} · <b>UNSPSC:</b> {p.unspsc}<br>"
            f"<b>Cierre de ofertas:</b> {p.fecha_cierre or 'sin fecha'}"
            f"{f' ({p.dias_restantes} días)' if p.dias_restantes is not None else ''} {p.etiqueta}<br>"
            f"<b>Tecnología:</b> puntaje {p.puntaje_tec} – {', '.join(p.terminos_tec) or 'sin términos'}<br>"
            f"<b>ID:</b> {p.id} · <b>Referencia:</b> {p.referencia} · <b>Publicado:</b> {p.fecha_publicacion}<br>"
            f"<a href='{p.url}'>{p.url}</a><p>{p.descripcion}</p>"
        )

    def _abrir_proceso(self, p: Proceso | None, descargar: bool) -> None:
        if not p:
            return
        if not p.url:
            QMessageBox.information(self, "Sin enlace", "Este proceso no tiene URL en SECOP II.")
            return
        self.navegador.abrir(p.url, p.id, descargar)

    def _preparar_oferta(self, p: Proceso | None) -> None:
        self.ofertas.preparar(p)
        if p:
            self.tabs.setCurrentWidget(self.ofertas)

    def _notificar(self, titulo: str, texto: str) -> None:
        if self.tray:
            self.tray.showMessage(titulo, texto)

    def _estado(self, msg: str) -> None:
        self.statusBar().showMessage(msg)
        self._log(f"[navegador] {msg}")

    def _captcha(self, esperando: bool) -> None:
        if esperando:
            self.statusBar().setStyleSheet("background:#ffe08a;color:#000")
            QApplication.alert(self)
            if self.tray:
                self.tray.showMessage("SECOP II", "Resuelve el captcha en la ventana del navegador.")
        else:
            self.statusBar().setStyleSheet("")

    # ---------------------------------------------------------------- Perfiles
    def _guardar_perfil(self) -> None:
        nombre, ok = QInputDialog.getText(self, "Guardar perfil", "Nombre del perfil:")
        nombre = nombre.strip()
        if not ok or not nombre:
            return
        existentes = {p.nombre for p in self.storage.perfiles()}
        if nombre in existentes and QMessageBox.question(
            self, "Reemplazar", f"Ya existe «{nombre}». ¿Reemplazar sus filtros?"
        ) != QMessageBox.Yes:
            return
        self.storage.guardar_perfil(nombre, self.editor.busqueda())
        self._refrescar_perfiles()
        self.statusBar().showMessage(f"Perfil «{nombre}» guardado.")

    def _tab_perfiles(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Marca la casilla de los perfiles que el monitor debe revisar."))
        self.lista_perfiles = QListWidget()
        self.lista_perfiles.itemChanged.connect(self._perfil_marcado)
        self.lista_perfiles.itemDoubleClicked.connect(lambda _: self._cargar_perfil())
        lay.addWidget(self.lista_perfiles, 1)
        bl = QHBoxLayout()
        for texto, fn in (
            ("Cargar en Buscar", self._cargar_perfil),
            ("Renombrar…", self._renombrar_perfil),
            ("Borrar", self._borrar_perfil),
        ):
            b = QPushButton(texto)
            b.clicked.connect(fn)
            bl.addWidget(b)
        bl.addStretch()
        lay.addLayout(bl)
        self._refrescar_perfiles()
        return w

    def _refrescar_perfiles(self) -> None:
        self.lista_perfiles.blockSignals(True)
        self.lista_perfiles.clear()
        for p in self.storage.perfiles():
            b = p.busqueda
            resumen = ", ".join(filter(None, [b.palabras_clave and f"«{b.palabras_clave}»", f"{len(b.filtros)} filtro(s)"]))
            item = QListWidgetItem(f"{p.nombre}   —   {resumen}")
            item.setData(Qt.UserRole, p)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if p.activo else Qt.Unchecked)
            self.lista_perfiles.addItem(item)
        self.lista_perfiles.blockSignals(False)

    def _perfil_sel(self):
        item = self.lista_perfiles.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _perfil_marcado(self, item: QListWidgetItem) -> None:
        p = item.data(Qt.UserRole)
        self.storage.activar_perfil(p.id, item.checkState() == Qt.Checked)

    def _cargar_perfil(self) -> None:
        p = self._perfil_sel()
        if p:
            self.editor.cargar(p.busqueda)
            self.tabs.setCurrentIndex(0)
            self._buscar()

    def _renombrar_perfil(self) -> None:
        p = self._perfil_sel()
        if not p:
            return
        nombre, ok = QInputDialog.getText(self, "Renombrar perfil", "Nuevo nombre:", text=p.nombre)
        if ok and nombre.strip():
            try:
                self.storage.renombrar_perfil(p.id, nombre.strip())
            except Exception as e:
                QMessageBox.warning(self, "No se pudo renombrar", str(e))
            self._refrescar_perfiles()

    def _borrar_perfil(self) -> None:
        p = self._perfil_sel()
        if p and QMessageBox.question(self, "Borrar", f"¿Borrar el perfil «{p.nombre}»?") == QMessageBox.Yes:
            self.storage.borrar_perfil(p.id)
            self._refrescar_perfiles()

    # -------------------------------------------------------------- Automático
    def _tab_auto(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        fila = QHBoxLayout()
        fila.addWidget(QLabel("Comprobar si hay datos nuevos cada"))
        self.intervalo = QSpinBox()
        self.intervalo.setRange(5, 1440)
        self.intervalo.setSuffix(" min")
        self.intervalo.setValue(int(self.ajustes.get("intervalo_minutos", 15)))
        fila.addWidget(self.intervalo)
        self.btn_auto = QPushButton("Iniciar")
        self.btn_auto.clicked.connect(self._alternar_auto)
        fila.addWidget(self.btn_auto)
        b_ahora = QPushButton("Revisar ahora")
        b_ahora.clicked.connect(lambda: self.programador.ejecutar_ahora(forzar=True))
        fila.addWidget(b_ahora)
        fila.addStretch()
        lay.addLayout(fila)
        lay.addWidget(
            QLabel(
                "datos.gov.co publica los datos de SECOP II una vez al día. El monitor comprueba cada pocos minutos "
                "si hubo una carga nueva y, en cuanto la hay, revisa los perfiles activos y te avisa (bandeja y correo).\n"
                "La primera revisión de cada perfil solo registra lo que ya existe; desde la segunda se notifican los nuevos, "
                "ordenados por prioridad: ⚡ mínima cuantía primero, luego los que cierran antes."
            )
        )
        split = QSplitter(Qt.Vertical)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        split.addWidget(self.log)
        cont = QWidget()
        cl = QVBoxLayout(cont)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(QLabel("Nuevas licitaciones detectadas en esta sesión:"))
        self.tabla_nuevos = TablaProcesos()
        self.tabla_nuevos.abrir.connect(self._abrir_proceso)
        self.tabla_nuevos.preparar_oferta.connect(self._preparar_oferta)
        cl.addWidget(self.tabla_nuevos)
        split.addWidget(cont)
        lay.addWidget(split, 1)
        return w

    def _alternar_auto(self) -> None:
        if self.programador.activo:
            self.programador.detener()
        else:
            self.ajustes["intervalo_minutos"] = self.intervalo.value()
            config.guardar(self.ajustes)
            self.programador.iniciar(self.intervalo.value())

    def _auto_corriendo(self, si: bool) -> None:
        self.btn_auto.setText("Detener" if si else "Iniciar")
        self.intervalo.setEnabled(not si)

    def _log(self, msg: str) -> None:
        from datetime import datetime

        self.log.appendPlainText(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}")

    def _nuevos(self, perfil: str, procesos: list[Proceso]) -> None:
        self._nuevos_acumulados = procesos + self._nuevos_acumulados
        self.tabla_nuevos.poner(self._nuevos_acumulados, {p.id for p in self._nuevos_acumulados})
        if self.tray:
            rapidas = sum(p.es_rapida for p in procesos)
            extra = f" (⚡ {rapidas} de mínima cuantía)" if rapidas else ""
            self.tray.showMessage("SECOP II", f"{len(procesos)} convocatoria(s) nueva(s) en «{perfil}»{extra}")
            QApplication.alert(self)
        self.ofertas.evaluar_nuevos(procesos)

    # ----------------------------------------------------------------- Ajustes
    def _tab_ajustes(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        g_api = QGroupBox("Datos abiertos (datos.gov.co)")
        f = QFormLayout(g_api)
        self.app_token = QLineEdit(self.ajustes.get("app_token", ""))
        self.app_token.setPlaceholderText("Opcional: aumenta el límite de peticiones")
        f.addRow("App Token:", self.app_token)
        self.max_res = QSpinBox()
        self.max_res.setRange(100, 50000)
        self.max_res.setSingleStep(500)
        self.max_res.setValue(int(self.ajustes.get("max_resultados", 2000)))
        f.addRow("Máximo de resultados:", self.max_res)
        lay.addWidget(g_api)

        smtp = self.ajustes["smtp"]
        g = QGroupBox("Correo (SMTP)")
        f = QFormLayout(g)
        self.smtp_servidor = QLineEdit(smtp["servidor"])
        self.smtp_puerto = QSpinBox()
        self.smtp_puerto.setRange(1, 65535)
        self.smtp_puerto.setValue(int(smtp["puerto"]))
        self.smtp_seg = QComboBox()
        self.smtp_seg.addItems(["STARTTLS", "SSL", "Ninguna"])
        self.smtp_seg.setCurrentText(smtp["seguridad"])
        self.smtp_usuario = QLineEdit(smtp["usuario"])
        self.smtp_pass = QLineEdit(smtp["password"])
        self.smtp_pass.setEchoMode(QLineEdit.Password)
        self.smtp_pass.setPlaceholderText("En Gmail usa una «contraseña de aplicación»")
        self.smtp_rem = QLineEdit(smtp["remitente"])
        self.smtp_rem.setPlaceholderText("Por defecto, el usuario")
        self.smtp_dest = QLineEdit(smtp["destinatarios"])
        self.smtp_dest.setPlaceholderText("correo1@ejemplo.com, correo2@ejemplo.com")
        f.addRow("Servidor:", self.smtp_servidor)
        f.addRow("Puerto:", self.smtp_puerto)
        f.addRow("Seguridad:", self.smtp_seg)
        f.addRow("Usuario:", self.smtp_usuario)
        f.addRow("Contraseña:", self.smtp_pass)
        f.addRow("Remitente:", self.smtp_rem)
        f.addRow("Destinatarios:", self.smtp_dest)
        lay.addWidget(g)

        imap = imap_desde(self.ajustes)
        g_imap = QGroupBox("Tiempo real: correos de SECOP II (IMAP)")
        f = QFormLayout(g_imap)
        nota = QLabel(
            "En tu cuenta de proveedor SECOP II configura «áreas de interés» con tus códigos UNSPSC; SECOP te "
            "envía un correo apenas se crea un proceso. El bot lee ese buzón (sin marcar como leído) y te avisa al instante."
        )
        nota.setWordWrap(True)
        f.addRow(nota)
        self.imap_activo = QCheckBox("Vigilar el buzón")
        self.imap_activo.setChecked(imap.activo)
        self.imap_servidor = QLineEdit(imap.servidor)
        self.imap_usuario = QLineEdit(self.ajustes.get("imap", {}).get("usuario", ""))
        self.imap_usuario.setPlaceholderText("Por defecto, el usuario SMTP")
        self.imap_pass = QLineEdit()
        self.imap_pass.setEchoMode(QLineEdit.Password)
        self.imap_pass.setPlaceholderText("Vacía = la guardada, o la de SMTP si es el mismo usuario")
        self.imap_filtro = QLineEdit(imap.filtro_remitente)
        self.imap_filtro.setToolTip("Texto que debe contener el remitente de los correos de SECOP")
        self.imap_intervalo = QSpinBox()
        self.imap_intervalo.setRange(30, 3600)
        self.imap_intervalo.setSuffix(" s")
        self.imap_intervalo.setValue(int(imap.intervalo_seg))
        f.addRow("", self.imap_activo)
        f.addRow("Servidor IMAP:", self.imap_servidor)
        f.addRow("Usuario:", self.imap_usuario)
        f.addRow("Contraseña:", self.imap_pass)
        f.addRow("Remitente contiene:", self.imap_filtro)
        f.addRow("Revisar cada:", self.imap_intervalo)
        lay.addWidget(g_imap)

        bl = QHBoxLayout()
        b_guardar = QPushButton("Guardar ajustes")
        b_guardar.clicked.connect(self._guardar_ajustes)
        b_prueba = QPushButton("Enviar correo de prueba")
        b_prueba.clicked.connect(self._correo_prueba)
        bl.addWidget(b_guardar)
        bl.addWidget(b_prueba)
        bl.addStretch()
        lay.addLayout(bl)

        self.chk_bandeja = QCheckBox("Al cerrar la ventana, seguir en la bandeja si la búsqueda automática está activa")
        self.chk_bandeja.setChecked(bool(self.ajustes.get("minimizar_a_bandeja", True)))
        lay.addWidget(self.chk_bandeja)
        self.chk_auto = QCheckBox("Iniciar el monitor automáticamente al abrir la aplicación")
        self.chk_auto.setChecked(bool(self.ajustes.get("auto_iniciar", True)))
        lay.addWidget(self.chk_auto)
        lay.addStretch()
        return w

    def _leer_ajustes(self) -> None:
        self.ajustes["app_token"] = self.app_token.text().strip()
        self.ajustes["max_resultados"] = self.max_res.value()
        self.ajustes["minimizar_a_bandeja"] = self.chk_bandeja.isChecked()
        self.ajustes["auto_iniciar"] = self.chk_auto.isChecked()
        self.ajustes["smtp"] = {
            "servidor": self.smtp_servidor.text().strip(),
            "puerto": self.smtp_puerto.value(),
            "seguridad": self.smtp_seg.currentText(),
            "usuario": self.smtp_usuario.text().strip(),
            "password": self.smtp_pass.text(),
            "remitente": self.smtp_rem.text().strip(),
            "destinatarios": self.smtp_dest.text().strip(),
        }
        usuario_imap = self.imap_usuario.text().strip()
        if self.imap_pass.text():
            config.guardar_secreto(f"imap:{usuario_imap or self.ajustes['smtp']['usuario']}", self.imap_pass.text())
        self.ajustes["imap"] = {
            "activo": self.imap_activo.isChecked(),
            "servidor": self.imap_servidor.text().strip(),
            "puerto": 993,
            "usuario": usuario_imap,
            "filtro_remitente": self.imap_filtro.text().strip(),
            "intervalo_seg": self.imap_intervalo.value(),
        }
        self.editor.app_token = self.ajustes["app_token"]

    def _guardar_ajustes(self) -> None:
        self._leer_ajustes()
        en_llavero = config.guardar(self.ajustes)
        self.vigilante.detener()
        if self.ajustes["imap"]["activo"]:
            self.vigilante.iniciar()
        msg = "Ajustes guardados."
        if self.ajustes["smtp"]["password"] and not en_llavero:
            msg += " Aviso: no hay llavero del sistema disponible; la contraseña quedó en settings.json (permisos 600)."
        self.statusBar().showMessage(msg)

    def _correo_prueba(self) -> None:
        self._leer_ajustes()
        cfg = config.smtp_desde(self.ajustes)
        ejecutar(
            Tarea(notifier.enviar, cfg, "Prueba – Bot SECOP II", "El correo del bot de licitaciones funciona."),
            lambda _: QMessageBox.information(self, "Correo", "Correo de prueba enviado."),
            lambda e: QMessageBox.critical(self, "Correo", f"No se pudo enviar: {e}"),
        )

    # ------------------------------------------------------------------ cierre
    def _salir(self) -> None:
        self.programador.detener()
        self.close()

    def closeEvent(self, ev) -> None:
        if self.programador.activo and self.tray and self.chk_bandeja.isChecked():
            self.hide()
            self.tray.showMessage("SECOP II", "El bot sigue revisando en segundo plano.")
            ev.ignore()
            return
        self.navegador.responder(False)  # por si había una confirmación de envío pendiente
        self.navegador.cerrar()
        self.navegador.wait(3000)
        ev.accept()
        QApplication.quit()
