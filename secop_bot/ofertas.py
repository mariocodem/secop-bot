"""Cola de ofertas, documentos y reglas de envío automático.

Una oferta queda asociada a un proceso (id + URL), un valor ofertado y una lista de
documentos: los habilitantes de la biblioteca (RUT, cámara de comercio, RUP…) más los
de la carpeta propia del proceso (propuesta técnica, oferta económica…).
"""

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from .config import APP_DIR
from .models import Proceso
from .tecnologia import PUNTAJE_MINIMO

BIBLIOTECA_DIR = APP_DIR / "documentos"  # documentos habilitantes que van en todas las ofertas
OFERTAS_DIR = Path.home() / "SECOP" / "ofertas"  # una carpeta por proceso
EVIDENCIAS_DIR = APP_DIR / "evidencias"  # capturas de cada paso del envío

EXTENSIONES = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".jpg", ".jpeg", ".png"}

MODOS = {
    "simulacion": "Simulación: llena todo y se detiene antes de «Presentar»",
    "confirmar": "Confirmación: llena todo y pregunta antes de «Presentar»",
    "automatico": "Automático: presenta la oferta sin intervención",
}

ESTADOS = ("pendiente", "en_proceso", "lista", "enviada", "error", "descartada")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS ofertas (
    proceso_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    nombre TEXT,
    entidad TEXT,
    modalidad TEXT,
    precio_base REAL,
    valor_oferta REAL NOT NULL,
    fecha_cierre TEXT,
    origen TEXT NOT NULL,          -- manual | regla
    estado TEXT NOT NULL DEFAULT 'pendiente',
    detalle TEXT DEFAULT '',
    creada TEXT NOT NULL,
    enviada TEXT
);
"""


@dataclass
class Oferta:
    proceso_id: str
    url: str
    nombre: str
    entidad: str
    modalidad: str
    precio_base: float
    valor_oferta: float
    fecha_cierre: str
    origen: str = "manual"
    estado: str = "pendiente"
    detalle: str = ""
    creada: str = ""
    enviada: str | None = None
    documentos: list[Path] = field(default_factory=list)

    @property
    def carpeta(self) -> Path:
        return OFERTAS_DIR / self.proceso_id.replace("/", "_")


@dataclass
class Reglas:
    activo: bool = False
    modalidades: list[str] = field(default_factory=lambda: ["Mínima cuantía"])
    precio_max: float = 100_000_000
    puntaje_min: int = PUNTAJE_MINIMO + 3
    porcentaje: float = 95.0  # valor ofertado = % del precio base
    max_por_dia: int = 3
    requiere_carpeta: bool = True  # solo si existe carpeta del proceso con documentos propios

    @classmethod
    def desde(cls, d: dict | None) -> "Reglas":
        d = d or {}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def documentos_de(oferta: Oferta) -> list[Path]:
    """Biblioteca + carpeta del proceso (sin duplicar por nombre; manda la del proceso)."""
    docs: dict[str, Path] = {}
    for carpeta in (BIBLIOTECA_DIR, oferta.carpeta):
        if carpeta.is_dir():
            for f in sorted(carpeta.iterdir()):
                if f.is_file() and f.suffix.lower() in EXTENSIONES:
                    docs[f.name] = f
    return list(docs.values())


def evaluar_reglas(p: Proceso, reglas: Reglas, enviadas_hoy: int) -> tuple[bool, str]:
    """¿Se puede ofertar automáticamente este proceso? Devuelve (sí/no, motivo)."""
    if not reglas.activo:
        return False, "reglas automáticas desactivadas"
    if enviadas_hoy >= reglas.max_por_dia:
        return False, f"límite diario alcanzado ({reglas.max_por_dia})"
    if reglas.modalidades and not any(p.modalidad.lower().startswith(m.lower()) for m in reglas.modalidades):
        return False, f"modalidad «{p.modalidad}» no permitida"
    if not p.precio_base:
        return False, "sin precio base"
    if p.precio_base > reglas.precio_max:
        return False, "precio base mayor al máximo"
    if p.puntaje_tec < reglas.puntaje_min:
        return False, f"puntaje de tecnología {p.puntaje_tec} < {reglas.puntaje_min}"
    if p.dias_restantes is None or p.dias_restantes < 0:
        return False, "sin fecha de cierre vigente"
    if reglas.requiere_carpeta:
        carpeta = OFERTAS_DIR / p.id
        if not carpeta.is_dir() or not any(f.suffix.lower() in EXTENSIONES for f in carpeta.iterdir()):
            return False, f"falta la carpeta de documentos {carpeta}"
    return True, "cumple las reglas"


def nueva_oferta(p: Proceso, valor: float, origen: str = "manual") -> Oferta:
    return Oferta(
        proceso_id=p.id,
        url=p.url,
        nombre=p.nombre,
        entidad=p.entidad,
        modalidad=p.modalidad,
        precio_base=p.precio_base,
        valor_oferta=round(valor),
        fecha_cierre=p.fecha_cierre,
        origen=origen,
        creada=datetime.now().isoformat(timespec="seconds"),
    )


class ColaOfertas:
    def __init__(self, ruta: Path):
        self.ruta = ruta
        for d in (BIBLIOTECA_DIR, OFERTAS_DIR, EVIDENCIAS_DIR):
            d.mkdir(parents=True, exist_ok=True)
        with self._con() as con:
            con.executescript(ESQUEMA)

    def _con(self) -> sqlite3.Connection:
        return sqlite3.connect(self.ruta)

    def agregar(self, o: Oferta) -> bool:
        """Agrega la oferta; False si ya existía una para ese proceso (nunca se duplica)."""
        with self._con() as con:
            cur = con.execute(
                "INSERT OR IGNORE INTO ofertas (proceso_id, url, nombre, entidad, modalidad, precio_base, "
                "valor_oferta, fecha_cierre, origen, estado, creada) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (o.proceso_id, o.url, o.nombre, o.entidad, o.modalidad, o.precio_base,
                 o.valor_oferta, o.fecha_cierre, o.origen, o.estado, o.creada),
            )
            return bool(cur.rowcount)

    def todas(self) -> list[Oferta]:
        with self._con() as con:
            filas = con.execute(
                "SELECT proceso_id, url, nombre, entidad, modalidad, precio_base, valor_oferta, fecha_cierre, "
                "origen, estado, detalle, creada, enviada FROM ofertas ORDER BY creada DESC"
            ).fetchall()
        return [Oferta(*f) for f in filas]

    def obtener(self, proceso_id: str) -> Oferta | None:
        return next((o for o in self.todas() if o.proceso_id == proceso_id), None)

    def actualizar(self, proceso_id: str, estado: str, detalle: str = "") -> None:
        enviada = datetime.now().isoformat(timespec="seconds") if estado == "enviada" else None
        with self._con() as con:
            con.execute(
                "UPDATE ofertas SET estado = ?, detalle = ?, enviada = COALESCE(?, enviada) WHERE proceso_id = ?",
                (estado, detalle, enviada, proceso_id),
            )

    def cambiar_valor(self, proceso_id: str, valor: float) -> None:
        with self._con() as con:
            con.execute("UPDATE ofertas SET valor_oferta = ? WHERE proceso_id = ?", (round(valor), proceso_id))

    def borrar(self, proceso_id: str) -> None:
        with self._con() as con:
            con.execute("DELETE FROM ofertas WHERE proceso_id = ? AND estado != 'enviada'", (proceso_id,))

    def automaticas_hoy(self) -> int:
        with self._con() as con:
            (n,) = con.execute(
                "SELECT count(*) FROM ofertas WHERE origen = 'regla' AND substr(creada, 1, 10) = ?",
                (date.today().isoformat(),),
            ).fetchone()
        return n
