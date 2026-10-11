"""Estructuras de datos del bot."""

import json
from dataclasses import asdict, dataclass, field
from datetime import date

# Modalidades donde, agotados los criterios legales de desempate, puede definir el
# orden de llegada de la oferta: conviene enterarse y ofertar cuanto antes.
MODALIDADES_RAPIDAS = ("mínima cuantía",)
# Solicitudes de información: estudio de mercado previo, señal temprana de un proceso.
MODALIDADES_SENAL = ("solicitud de información",)


@dataclass
class Filtro:
    campo: str
    operador: str
    valor: str
    valor2: str = ""


@dataclass
class Busqueda:
    palabras_clave: str = ""
    solo_abiertos: bool = True  # "solo vigentes": publicado y con fecha de cierre futura
    solo_tecnologia: bool = True
    filtros: list[Filtro] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, texto: str) -> "Busqueda":
        d = json.loads(texto)
        return cls(
            palabras_clave=d.get("palabras_clave", ""),
            solo_abiertos=d.get("solo_abiertos", True),
            solo_tecnologia=d.get("solo_tecnologia", False),
            filtros=[Filtro(**f) for f in d.get("filtros", [])],
        )


@dataclass
class Perfil:
    id: int | None
    nombre: str
    busqueda: Busqueda
    activo: bool = False
    inicializado: bool = False


@dataclass
class Proceso:
    id: str
    referencia: str
    entidad: str
    departamento: str
    ciudad: str
    nombre: str
    descripcion: str
    modalidad: str
    tipo_contrato: str
    fase: str
    estado: str
    precio_base: float
    fecha_publicacion: str
    fecha_cierre: str
    url: str
    unspsc: str
    puntaje_tec: int = 0
    terminos_tec: list[str] = field(default_factory=list)

    @property
    def dias_restantes(self) -> int | None:
        if not self.fecha_cierre:
            return None
        return (date.fromisoformat(self.fecha_cierre) - date.today()).days

    @property
    def es_rapida(self) -> bool:
        return self.modalidad.lower().startswith(MODALIDADES_RAPIDAS)

    @property
    def es_senal(self) -> bool:
        return self.modalidad.lower().startswith(MODALIDADES_SENAL)

    @property
    def etiqueta(self) -> str:
        if self.es_rapida:
            return "⚡ Mínima cuantía"
        if self.es_senal:
            return "📡 Señal temprana (RFI)"
        return ""

    @classmethod
    def from_row(cls, r: dict) -> "Proceso":
        url = r.get("urlproceso", "")
        if isinstance(url, dict):
            url = url.get("url", "")
        try:
            precio = float(r.get("precio_base") or 0)
        except ValueError:
            precio = 0.0
        return cls(
            id=r.get("id_del_proceso", ""),
            referencia=r.get("referencia_del_proceso", ""),
            entidad=r.get("entidad", ""),
            departamento=r.get("departamento_entidad", ""),
            ciudad=r.get("ciudad_entidad", ""),
            nombre=r.get("nombre_del_procedimiento", ""),
            descripcion=r.get("descripci_n_del_procedimiento", ""),
            modalidad=r.get("modalidad_de_contratacion", ""),
            tipo_contrato=r.get("tipo_de_contrato", ""),
            fase=r.get("fase", ""),
            estado=r.get("estado_del_procedimiento", ""),
            precio_base=precio,
            fecha_publicacion=(r.get("fecha_de_publicacion_del") or "")[:10],
            fecha_cierre=(r.get("fecha_de_recepcion_de") or "")[:10],
            url=url,
            unspsc=r.get("codigo_principal_de_categoria", ""),
        )


@dataclass
class SmtpConfig:
    servidor: str = "smtp.gmail.com"
    puerto: int = 587
    seguridad: str = "STARTTLS"  # STARTTLS | SSL | Ninguna
    usuario: str = ""
    password: str = ""
    remitente: str = ""
    destinatarios: str = ""
