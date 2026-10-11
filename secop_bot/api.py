"""Cliente del dataset abierto "SECOP II - Procesos de Contratación" (datos.gov.co, Socrata/SoQL)."""

from datetime import date, timedelta

import requests

from . import tecnologia
from .models import Busqueda, Filtro, Proceso

URL = "https://www.datos.gov.co/resource/p6dx-8zbt.json"
URL_META = "https://www.datos.gov.co/api/views/p6dx-8zbt.json"
PAGINA = 1000
TIMEOUT = 60

# campo -> (etiqueta, tipo)
CAMPOS: dict[str, tuple[str, str]] = {
    "nombre_del_procedimiento": ("Nombre del proceso", "texto"),
    "descripci_n_del_procedimiento": ("Descripción", "texto"),
    "entidad": ("Entidad", "texto"),
    "nit_entidad": ("NIT entidad", "texto"),
    "departamento_entidad": ("Departamento", "texto"),
    "ciudad_entidad": ("Ciudad", "texto"),
    "modalidad_de_contratacion": ("Modalidad", "texto"),
    "tipo_de_contrato": ("Tipo de contrato", "texto"),
    "fase": ("Fase", "texto"),
    "estado_del_procedimiento": ("Estado del procedimiento", "texto"),
    "codigo_principal_de_categoria": ("Código UNSPSC", "texto"),
    "ordenentidad": ("Orden (Nacional/Territorial)", "texto"),
    "precio_base": ("Precio base", "numero"),
    "duracion": ("Duración", "numero"),
    "fecha_de_publicacion_del": ("Fecha de publicación", "fecha"),
    "fecha_de_recepcion_de": ("Fecha de cierre (ofertas)", "fecha"),
}

# Campos cuyos valores posibles se pueden listar en un combo
CAMPOS_CON_LISTA = {
    "departamento_entidad",
    "modalidad_de_contratacion",
    "tipo_de_contrato",
    "fase",
    "estado_del_procedimiento",
    "ordenentidad",
}

OPERADORES: dict[str, list[str]] = {
    "texto": ["contiene", "no contiene", "igual a", "empieza por"],
    "numero": ["≥", "≤", "entre"],
    "fecha": ["desde", "hasta", "últimos N días"],
}


# "Vigente" = publicado y con fecha de recepción de ofertas de hoy en adelante.
# Las modalidades sin ofertas (contratación directa, régimen especial) no traen esa
# fecha: no son convocatorias a las que se pueda presentar oferta.


class ErrorApi(Exception):
    pass


def _q(texto: str) -> str:
    """Literal SoQL con comillas simples escapadas."""
    return "'" + texto.replace("'", "''") + "'"


def _num(texto: str) -> str:
    limpio = texto.replace(".", "").replace(",", "").replace("$", "").strip()
    if not limpio.isdigit():
        raise ValueError(f"Valor numérico inválido: {texto!r}")
    return limpio


def _fecha(texto: str) -> str:
    return date.fromisoformat(texto.strip()).isoformat() + "T00:00:00"


def condicion(f: Filtro) -> str:
    if f.campo not in CAMPOS:
        raise ValueError(f"Campo desconocido: {f.campo}")
    tipo = CAMPOS[f.campo][1]
    c, v = f.campo, f.valor.strip()
    if tipo == "texto":
        up = v.upper()
        return {
            "contiene": f"upper({c}) like {_q('%' + up + '%')}",
            "no contiene": f"upper({c}) not like {_q('%' + up + '%')}",
            "igual a": f"{c} = {_q(v)}",
            "empieza por": f"upper({c}) like {_q(up + '%')}",
        }[f.operador]
    if tipo == "numero":
        if f.operador == "≥":
            return f"{c} >= {_num(v)}"
        if f.operador == "≤":
            return f"{c} <= {_num(v)}"
        return f"{c} between {_num(v)} and {_num(f.valor2)}"
    # fecha
    if f.operador == "desde":
        return f"{c} >= {_q(_fecha(v))}"
    if f.operador == "hasta":
        return f"{c} <= {_q(_fecha(v).replace('T00:00:00', 'T23:59:59'))}"
    dias = int(v)
    return f"{c} >= {_q((date.today() - timedelta(days=dias)).isoformat() + 'T00:00:00')}"


def construir_where(b: Busqueda) -> str:
    partes: list[str] = []
    if b.solo_abiertos:
        hoy = date.today().isoformat() + "T00:00:00"
        partes.append(f"estado_del_procedimiento = 'Publicado' AND fecha_de_recepcion_de >= {_q(hoy)}")
    palabras = [p.strip().upper() for p in b.palabras_clave.split(",") if p.strip()]
    if palabras:
        ors = []
        for p in palabras:
            lit = _q("%" + p + "%")
            ors.append(f"upper(nombre_del_procedimiento) like {lit}")
            ors.append(f"upper(descripci_n_del_procedimiento) like {lit}")
        partes.append("(" + " OR ".join(ors) + ")")
    for f in b.filtros:
        if f.valor.strip() or (f.operador == "entre" and f.valor2.strip()):
            partes.append(condicion(f))
    return " AND ".join(partes)


def _get(params: dict, app_token: str = "") -> list[dict]:
    headers = {"X-App-Token": app_token} if app_token else {}
    try:
        r = requests.get(URL, params=params, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise ErrorApi(f"No se pudo conectar con datos.gov.co: {e}") from e
    if r.status_code != 200:
        try:
            msg = r.json().get("message", r.text)
        except ValueError:
            msg = r.text
        raise ErrorApi(f"Error {r.status_code} de la API: {msg[:300]}")
    return r.json()


# Tope de filas crudas cuando el filtro de tecnología se aplica localmente
# (las convocatorias vigentes rondan las 3.000).
MAX_CRUDO_TECNOLOGIA = 20000


def buscar(b: Busqueda, max_resultados: int = 2000, app_token: str = "", progreso=None) -> list[Proceso]:
    where = construir_where(b)
    procesos: list[Proceso] = []
    vistos: set[str] = set()
    tope = max(max_resultados, MAX_CRUDO_TECNOLOGIA) if b.solo_tecnologia else max_resultados
    offset = 0
    while offset < tope:
        params = {
            "$limit": min(PAGINA, tope - offset),
            "$offset": offset,
            "$order": "fecha_de_publicacion_del DESC, id_del_proceso",
        }
        if where:
            params["$where"] = where
        filas = _get(params, app_token)
        for fila in filas:
            p = Proceso.from_row(fila)
            if p.id and p.id not in vistos:
                vistos.add(p.id)
                procesos.append(p)
        if progreso:
            progreso(len(procesos))
        if len(filas) < params["$limit"]:
            break
        offset += len(filas)
    tecnologia.anotar(procesos)
    if b.solo_tecnologia:
        procesos = [p for p in procesos if p.puntaje_tec >= tecnologia.PUNTAJE_MINIMO][:max_resultados]
    return procesos


def ultima_actualizacion(app_token: str = "") -> int:
    """Marca de tiempo (epoch) de la última carga de datos del dataset."""
    headers = {"X-App-Token": app_token} if app_token else {}
    try:
        r = requests.get(URL_META, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        return int(r.json().get("rowsUpdatedAt", 0))
    except (requests.RequestException, ValueError) as e:
        raise ErrorApi(f"No se pudo consultar la actualización del dataset: {e}") from e


def valores_distintos(campo: str, app_token: str = "") -> list[str]:
    filas = _get(
        {"$select": f"{campo} AS v", "$group": campo, "$order": campo, "$limit": 500},
        app_token,
    )
    return [f["v"] for f in filas if f.get("v")]
