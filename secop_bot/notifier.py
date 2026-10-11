"""Envío de notificaciones por correo (SMTP)."""

import smtplib
import ssl
from email.message import EmailMessage
from html import escape

from .models import Proceso, SmtpConfig


def _destinatarios(cfg: SmtpConfig) -> list[str]:
    return [d.strip() for d in cfg.destinatarios.replace(";", ",").split(",") if d.strip()]


def enviar(cfg: SmtpConfig, asunto: str, texto: str, html: str | None = None) -> None:
    destinos = _destinatarios(cfg)
    if not cfg.servidor or not destinos:
        raise ValueError("Configura el servidor SMTP y al menos un destinatario.")
    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = cfg.remitente or cfg.usuario
    msg["To"] = ", ".join(destinos)
    msg.set_content(texto)
    if html:
        msg.add_alternative(html, subtype="html")

    ctx = ssl.create_default_context()
    if cfg.seguridad == "SSL":
        servidor = smtplib.SMTP_SSL(cfg.servidor, int(cfg.puerto), context=ctx, timeout=30)
    else:
        servidor = smtplib.SMTP(cfg.servidor, int(cfg.puerto), timeout=30)
    with servidor:
        if cfg.seguridad == "STARTTLS":
            servidor.starttls(context=ctx)
        if cfg.usuario:
            servidor.login(cfg.usuario, cfg.password)
        servidor.send_message(msg)


def _pesos(valor: float) -> str:
    return "$" + f"{valor:,.0f}".replace(",", ".")


def _cierre(p: Proceso) -> str:
    if p.dias_restantes is None:
        return "sin fecha"
    if p.dias_restantes == 0:
        return f"{p.fecha_cierre} (¡HOY!)"
    return f"{p.fecha_cierre} ({p.dias_restantes} d)"


def enviar_nuevos(cfg: SmtpConfig, perfil: str, procesos: list[Proceso]) -> None:
    rapidas = sum(p.es_rapida for p in procesos)
    prefijo = f"⚡ {rapidas} de mínima cuantía · " if rapidas else ""
    asunto = f"SECOP II: {prefijo}{len(procesos)} convocatoria(s) nueva(s) – {perfil}"
    lineas = [
        f"- {p.etiqueta + ' · ' if p.etiqueta else ''}{p.nombre}\n"
        f"  {p.entidad} ({p.departamento}) · {p.modalidad} · {_pesos(p.precio_base)} · cierre {_cierre(p)}\n"
        f"  {p.url}"
        for p in procesos
    ]
    texto = f"Nuevas licitaciones para el perfil «{perfil}»:\n\n" + "\n\n".join(lineas)
    filas = "".join(
        f"<tr{' style=\'background:#fff4cc\'' if p.es_rapida else ''}>"
        f"<td><b>{escape(_cierre(p))}</b></td>"
        f"<td>{escape(p.etiqueta)}</td>"
        f"<td><a href=\"{escape(p.url)}\">{escape(p.nombre)}</a><br><small>{escape(p.descripcion[:250])}</small></td>"
        f"<td>{escape(p.entidad)}<br><small>{escape(p.departamento)}</small></td>"
        f"<td>{escape(p.modalidad)}</td>"
        f"<td style='text-align:right'>{_pesos(p.precio_base)}</td>"
        "</tr>"
        for p in procesos
    )
    html = (
        f"<h3>Nuevas convocatorias vigentes – {escape(perfil)}</h3>"
        "<p>Ordenadas por prioridad: mínima cuantía primero, luego las que cierran antes.</p>"
        "<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;font-family:sans-serif;font-size:13px'>"
        "<tr><th>Cierre</th><th>Alerta</th><th>Proceso</th><th>Entidad</th><th>Modalidad</th><th>Precio base</th></tr>"
        f"{filas}</table>"
    )
    enviar(cfg, asunto, texto, html)
