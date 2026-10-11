"""Clasificador de procesos de tecnología.

El 97 % de los procesos vigentes no trae código UNSPSC (sale "UNSPECIFIED"), así que la
clasificación se hace sobre el texto (nombre + descripción) con términos ponderados,
sin tildes y respetando límites de palabra. El UNSPSC suma cuando sí viene.
"""

import re
import unicodedata

from .models import Proceso

PUNTAJE_MINIMO = 3

# (patrón regex sobre texto en minúsculas y sin tildes, peso)
TERMINOS: list[tuple[str, int]] = [
    # software y desarrollo
    (r"software", 4),
    (r"licenciamiento", 3),
    (r"licencias? (de )?(software|uso|antivirus|office|microsoft|oracle|adobe|autocad|arcgis|ibm|sap)", 4),
    (r"(renovacion|adquisicion|actualizacion|suscripcion)( y \w+)? (de )?(las )?licencias?", 3),
    (r"licencias?", 1),
    (r"endpoint|perdida de datos|\bdlp\b|\bedr\b|\bxdr\b", 4),
    (r"desarrollo (de )?(software|aplicaci\w+|sistemas?|plataforma|portal|web)", 5),
    (r"aplicaci(on|ones) (web|movil\w*|informatic\w*)", 4),
    (r"sistemas? de informacion", 4),
    (r"plataforma (tecnologica|digital|virtual|web|e-?learning|lms)", 4),
    (r"pagina web|portal web|sitio web|intranet", 4),
    (r"\berp\b|\bcrm\b|\bsaas\b|\bpaas\b|\biaas\b", 4),
    (r"base(s)? de datos", 3),
    (r"inteligencia artificial|machine learning|analitica de datos|big data|business intelligence", 5),
    (r"transformacion digital|gobierno digital|interoperabilidad", 4),
    (r"ciberseguridad|seguridad (de la )?informa\w+|seguridad digital|firewall|antivirus|\bsiem\b|\bsoc\b", 5),
    (r"nube|cloud|hosting|hospedaje|datacenter|centro de datos|data center", 4),
    (r"servidor(es)?(?! public)(?! del estado)", 3),
    (r"almacenamiento (de datos|en (la )?nube|masivo|digital)|backup|copias? de seguridad|virtualizacion|colocation", 3),
    (r"mesa de (ayuda|servicio)|soporte tecnico|help ?desk", 3),
    (r"\btic\b|tecnologias? de (la )?informacion|informatic\w+", 2),
    (r"(equipos|elementos|herramientas|infraestructura|soluciones?) tecnologic\w+", 3),
    (r"\bti\b|tecnologic\w+", 1),
    # hardware y redes
    (r"computador(es|as)?|portatil(es)?|equipos? de computo|tablets?|impresora\w*|escaner\w*", 4),
    (r"perifericos|hardware|monitores", 3),
    (r"redes? (de datos|lan|wan|wifi|inalambrica\w*|electrica\w* regulada\w*)|cableado estructurado|switch\w*|router\w*|access point", 4),
    (r"conectividad|internet|fibra optica|enlaces? (dedicad\w+|de datos)|canal(es)? de (datos|internet)", 4),
    (r"telecomunicaciones|telefonia ip|planta telefonica|\bvoip\b|radioenlace", 3),
    (r"camaras?|cctv|videovigilancia|control de acceso|biometric\w+", 2),
    (r"\bups\b", 1),
    (r"digitalizacion|gestion documental electronica|firma (digital|electronica)", 3),
]

# Términos que suelen indicar falsos positivos
NEGATIVOS: list[tuple[str, int]] = [
    (r"construccion|obra (civil|publica)|pavimentacion|via(s)? terciaria|alcantarillado|acueducto", -4),
    (r"vigilancia y seguridad privada|seguridad privada", -4),
    (r"uniformes|vestuario|dotacion de (los )?(servidores|funcionarios|trabajadores)|calzado", -4),
    (r"material didactico|medicamentos|dispensacion", -3),
    (r"elementos de proteccion personal|\bepp\b|servidores (publicos|del concejo|de la alcaldia)", -3),
    (r"interventoria", -2),
    (r"alimentacion|refrigerios|aseo y cafeteria|vigilancia (fisica|privada)|transporte (de pasajeros|aereo|terrestre)", -4),
    (r"prestacion de servicios profesionales y de apoyo a la gestion", -1),
]

# Prefijos UNSPSC (tras "V1.") de tecnología
UNSPSC_TEC = ("43", "8111", "8112", "8116", "8316", "4521", "4610")

_TERMINOS = [(re.compile(rf"\b(?:{p})\b"), w) for p, w in TERMINOS]
_NEGATIVOS = [(re.compile(rf"\b(?:{p})\b"), w) for p, w in NEGATIVOS]


def normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sin_tildes.lower()


def puntuar(p: Proceso) -> tuple[int, list[str]]:
    texto = normalizar(f"{p.nombre} {p.descripcion}")
    puntaje, encontrados = 0, []
    for rx, peso in _TERMINOS:
        m = rx.search(texto)
        if m:
            puntaje += peso
            encontrados.append(m.group(0))
    for rx, peso in _NEGATIVOS:
        if rx.search(texto):
            puntaje += peso
    codigo = p.unspsc.removeprefix("V1.")
    if codigo.startswith(UNSPSC_TEC):
        puntaje += 5
        encontrados.append(f"UNSPSC {codigo}")
    return puntaje, encontrados


def anotar(procesos: list[Proceso]) -> None:
    for p in procesos:
        p.puntaje_tec, p.terminos_tec = puntuar(p)
