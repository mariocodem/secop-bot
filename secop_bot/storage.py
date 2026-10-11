"""Persistencia en SQLite: perfiles de búsqueda y procesos ya vistos."""

import sqlite3
from datetime import datetime
from pathlib import Path

from .models import Busqueda, Perfil, Proceso

ESQUEMA = """
CREATE TABLE IF NOT EXISTS perfiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE,
    busqueda TEXT NOT NULL,
    activo INTEGER NOT NULL DEFAULT 0,
    inicializado INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS vistos (
    perfil_id INTEGER NOT NULL REFERENCES perfiles(id) ON DELETE CASCADE,
    proceso_id TEXT NOT NULL,
    nombre TEXT,
    entidad TEXT,
    url TEXT,
    visto_en TEXT NOT NULL,
    PRIMARY KEY (perfil_id, proceso_id)
);
"""


class Storage:
    def __init__(self, ruta: Path):
        ruta.parent.mkdir(parents=True, exist_ok=True)
        self.ruta = ruta
        with self._con() as con:
            con.executescript(ESQUEMA)

    def _con(self) -> sqlite3.Connection:
        # Una conexión por operación: se usa desde hilos distintos (interfaz y workers).
        con = sqlite3.connect(self.ruta)
        con.execute("PRAGMA foreign_keys = ON")
        return con

    # --- perfiles ---
    def perfiles(self) -> list[Perfil]:
        with self._con() as con:
            filas = con.execute(
                "SELECT id, nombre, busqueda, activo, inicializado FROM perfiles ORDER BY nombre"
            ).fetchall()
        return [Perfil(i, n, Busqueda.from_json(b), bool(a), bool(ini)) for i, n, b, a, ini in filas]

    def guardar_perfil(self, nombre: str, busqueda: Busqueda) -> None:
        """Crea el perfil o reemplaza sus filtros (reinicia el registro de vistos)."""
        with self._con() as con:
            fila = con.execute("SELECT id FROM perfiles WHERE nombre = ?", (nombre,)).fetchone()
            if fila:
                con.execute(
                    "UPDATE perfiles SET busqueda = ?, inicializado = 0 WHERE id = ?",
                    (busqueda.to_json(), fila[0]),
                )
                con.execute("DELETE FROM vistos WHERE perfil_id = ?", (fila[0],))
            else:
                con.execute(
                    "INSERT INTO perfiles (nombre, busqueda) VALUES (?, ?)",
                    (nombre, busqueda.to_json()),
                )

    def crear_perfil_inicial(self) -> None:
        """La primera vez crea y activa el perfil «Tecnología – vigentes»."""
        if self.perfiles():
            return
        self.guardar_perfil("Tecnología – vigentes", Busqueda(solo_abiertos=True, solo_tecnologia=True))
        with self._con() as con:
            con.execute("UPDATE perfiles SET activo = 1")

    def renombrar_perfil(self, perfil_id: int, nombre: str) -> None:
        with self._con() as con:
            con.execute("UPDATE perfiles SET nombre = ? WHERE id = ?", (nombre, perfil_id))

    def borrar_perfil(self, perfil_id: int) -> None:
        with self._con() as con:
            con.execute("DELETE FROM perfiles WHERE id = ?", (perfil_id,))

    def activar_perfil(self, perfil_id: int, activo: bool) -> None:
        with self._con() as con:
            con.execute("UPDATE perfiles SET activo = ? WHERE id = ?", (int(activo), perfil_id))

    # --- vistos ---
    def registrar_nuevos(self, perfil: Perfil, procesos: list[Proceso]) -> list[Proceso]:
        """Guarda los procesos y devuelve los que no se habían visto antes para este perfil."""
        ahora = datetime.now().isoformat(timespec="seconds")
        nuevos = []
        with self._con() as con:
            for p in procesos:
                cur = con.execute(
                    "INSERT OR IGNORE INTO vistos (perfil_id, proceso_id, nombre, entidad, url, visto_en) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (perfil.id, p.id, p.nombre, p.entidad, p.url, ahora),
                )
                if cur.rowcount:
                    nuevos.append(p)
            if not perfil.inicializado:
                con.execute("UPDATE perfiles SET inicializado = 1 WHERE id = ?", (perfil.id,))
        return nuevos if perfil.inicializado else []
