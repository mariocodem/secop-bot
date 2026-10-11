"""Alertas en tiempo real a partir de los correos de SECOP II.

SECOP II envía un correo a los proveedores cuando se crea un proceso con un código
UNSPSC de sus «áreas de interés» (Configuración de la cuenta → Suscripción a notificaciones).
Este módulo revisa el buzón por IMAP cada minuto (sin marcar los correos como leídos),
extrae los enlaces a los procesos, los clasifica y avisa al instante.
"""

import email
import imaplib
import re
from dataclasses import dataclass
from email.header import decode_header, make_header
from html import unescape

from PySide6.QtCore import QObject, QTimer, Signal

from . import config, tecnologia
from .gui.workers import Tarea, ejecutar
from .models import Proceso

RX_URL = re.compile(r"https://community\.secop\.gov\.co/[^\s\"'<>]*?(?:noticeUID|NoticeUID)=(CO1\.NTC\.\d+)[^\s\"'<>]*")
RX_ID = re.compile(r"\bCO1\.(?:NTC|REQ|PCCNTR)\.\d+\b")


@dataclass
class ImapConfig:
    activo: bool = False
    servidor: str = "imap.gmail.com"
    puerto: int = 993
    usuario: str = ""
    password: str = ""
    filtro_remitente: str = "secop"
    intervalo_seg: int = 60


def imap_desde(ajustes: dict) -> ImapConfig:
    d = dict(ajustes.get("imap", {}))
    cfg = ImapConfig(**{k: v for k, v in d.items() if k in ImapConfig.__dataclass_fields__})
    smtp = ajustes.get("smtp", {})
    if not cfg.usuario:
        cfg.usuario = smtp.get("usuario", "")
    if not cfg.password:
        cfg.password = config.leer_secreto(f"imap:{cfg.usuario}")
    if not cfg.password and cfg.usuario == smtp.get("usuario"):
        cfg.password = smtp.get("password", "")
    return cfg


def _texto(msg: email.message.Message) -> str:
    partes = []
    for parte in msg.walk():
        if parte.get_content_maintype() != "text":
            continue
        try:
            contenido = parte.get_payload(decode=True).decode(parte.get_content_charset() or "utf-8", "replace")
        except Exception:
            continue
        if parte.get_content_subtype() == "html":
            contenido = unescape(re.sub(r"<[^>]+>", " ", contenido.replace("&amp;", "&")))
        partes.append(contenido)
    return re.sub(r"[ \t]+", " ", "\n".join(partes))


def procesos_del_correo(asunto: str, cuerpo: str, cuerpo_crudo: str) -> list[Proceso]:
    """Un Proceso (mínimo) por cada enlace/identificador de SECOP II del correo."""
    urls = {m.group(1): m.group(0).replace("&amp;", "&") for m in RX_URL.finditer(cuerpo_crudo + "\n" + cuerpo)}
    for ident in RX_ID.findall(asunto + "\n" + cuerpo):
        urls.setdefault(
            ident,
            f"https://community.secop.gov.co/Public/Tendering/OpportunityDetail/Index?noticeUID={ident}"
            if ".NTC." in ident else "",
        )
    procesos = []
    for ident, url in urls.items():
        p = Proceso(
            id=ident, referencia="", entidad="", departamento="", ciudad="", nombre=asunto,
            descripcion=cuerpo[:2000], modalidad="", tipo_contrato="", fase="", estado="Publicado",
            precio_base=0.0, fecha_publicacion="", fecha_cierre="", url=url, unspsc="",
        )
        p.puntaje_tec, p.terminos_tec = tecnologia.puntuar(p)
        procesos.append(p)
    return procesos


def revisar_buzon(cfg: ImapConfig, ultimo_uid: int) -> tuple[int, list[Proceso]]:
    """Devuelve (uid más alto visto, procesos nuevos). Con ultimo_uid=0 solo inicializa."""
    if not cfg.servidor or not cfg.usuario or not cfg.password:
        raise ValueError("Configura servidor, usuario y contraseña IMAP.")
    with imaplib.IMAP4_SSL(cfg.servidor, int(cfg.puerto), timeout=30) as imap:
        imap.login(cfg.usuario, cfg.password)
        imap.select("INBOX", readonly=True)  # no marca como leídos
        criterio = f'(UID {ultimo_uid + 1}:* FROM "{cfg.filtro_remitente}")' if cfg.filtro_remitente \
            else f"(UID {ultimo_uid + 1}:*)"
        _, datos = imap.uid("search", None, criterio)
        uids = [int(u) for u in (datos[0] or b"").split() if int(u) > ultimo_uid]
        if not ultimo_uid:  # primera vez: no notificar el histórico
            _, todos = imap.uid("search", None, "ALL")
            maximo = max([int(u) for u in (todos[0] or b"").split()] or [0])
            return maximo, []
        procesos: list[Proceso] = []
        for uid in uids:
            _, partes = imap.uid("fetch", str(uid), "(BODY.PEEK[])")
            crudo = next((p[1] for p in partes if isinstance(p, tuple)), b"")
            msg = email.message_from_bytes(crudo)
            asunto = str(make_header(decode_header(msg.get("Subject", ""))))
            procesos += procesos_del_correo(asunto, _texto(msg), crudo.decode("utf-8", "replace"))
        return max(uids + [ultimo_uid]), procesos


class VigilanteCorreo(QObject):
    log = Signal(str)
    nuevos = Signal(str, list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.revisar)
        self._ocupado = False

    @property
    def activo(self) -> bool:
        return self.timer.isActive()

    def iniciar(self) -> None:
        cfg = imap_desde(config.cargar())
        self.timer.start(max(30, int(cfg.intervalo_seg)) * 1000)
        self.log.emit(f"Vigilando correos de SECOP II en {cfg.usuario} cada {cfg.intervalo_seg} s.")
        self.revisar()

    def detener(self) -> None:
        self.timer.stop()

    def revisar(self) -> None:
        if self._ocupado:
            return
        self._ocupado = True
        ejecutar(Tarea(self._ciclo), self._fin, self._error)

    def _ciclo(self):
        ajustes = config.cargar()
        cfg = imap_desde(ajustes)
        ultimo = int(ajustes.get("imap_ultimo_uid", 0))
        nuevo_uid, procesos = revisar_buzon(cfg, ultimo)
        if nuevo_uid != ultimo:
            ajustes = config.cargar()
            ajustes["imap_ultimo_uid"] = nuevo_uid
            config.guardar(ajustes)
        return ultimo, procesos

    def _fin(self, resultado) -> None:
        self._ocupado = False
        ultimo, procesos = resultado
        if not ultimo:
            self.log.emit("Buzón inicializado: se avisará de los correos de SECOP que lleguen desde ahora.")
        if procesos:
            procesos.sort(key=lambda p: -p.puntaje_tec)
            self.log.emit(f"[correo] {len(procesos)} oportunidad(es) recibidas de SECOP II.")
            self.nuevos.emit("Correo SECOP (tiempo real)", procesos)

    def _error(self, msg: str) -> None:
        self._ocupado = False
        self.log.emit(f"[correo] error revisando el buzón: {msg}")
