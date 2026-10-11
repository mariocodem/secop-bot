"""Rutas y ajustes persistentes de la aplicación."""

import json
import os
from dataclasses import asdict
from pathlib import Path

from .models import SmtpConfig

APP_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "secop-bot"
SETTINGS_FILE = APP_DIR / "settings.json"
DB_FILE = APP_DIR / "secop.db"
BROWSER_DIR = APP_DIR / "browser"
DOWNLOAD_DIR = Path.home() / "Descargas" / "secop"

KEYRING_SERVICE = "secop-bot"

DEFAULTS = {
    "app_token": "",
    "max_resultados": 2000,
    "intervalo_horas": 6.0,
    "smtp": asdict(SmtpConfig()),
}


def _keyring():
    try:
        import keyring

        keyring.get_keyring()
        return keyring
    except Exception:
        return None


def cargar() -> dict:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    datos = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            datos.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    smtp = {**DEFAULTS["smtp"], **datos.get("smtp", {})}
    if not smtp.get("password") and smtp.get("usuario"):
        kr = _keyring()
        if kr:
            try:
                smtp["password"] = kr.get_password(KEYRING_SERVICE, smtp["usuario"]) or ""
            except Exception:
                pass
    datos["smtp"] = smtp
    return datos


def guardar(datos: dict) -> bool:
    """Guarda los ajustes. Devuelve True si la contraseña quedó en el llavero del sistema."""
    APP_DIR.mkdir(parents=True, exist_ok=True)
    copia = json.loads(json.dumps(datos))
    smtp = copia.get("smtp", {})
    en_llavero = False
    if smtp.get("password") and smtp.get("usuario"):
        kr = _keyring()
        if kr:
            try:
                kr.set_password(KEYRING_SERVICE, smtp["usuario"], smtp["password"])
                smtp["password"] = ""
                en_llavero = True
            except Exception:
                pass
    SETTINGS_FILE.write_text(json.dumps(copia, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        SETTINGS_FILE.chmod(0o600)
    except OSError:
        pass
    return en_llavero


def smtp_desde(datos: dict) -> SmtpConfig:
    return SmtpConfig(**{k: v for k, v in datos.get("smtp", {}).items() if k in SmtpConfig.__dataclass_fields__})


def leer_secreto(clave: str) -> str:
    kr = _keyring()
    if kr:
        try:
            return kr.get_password(KEYRING_SERVICE, clave) or ""
        except Exception:
            pass
    return ""


def guardar_secreto(clave: str, valor: str) -> bool:
    """Guarda un secreto en el llavero del sistema. False si no hay llavero disponible."""
    kr = _keyring()
    if not kr:
        return False
    try:
        if valor:
            kr.set_password(KEYRING_SERVICE, clave, valor)
        else:
            kr.delete_password(KEYRING_SERVICE, clave)
        return True
    except Exception:
        return False
