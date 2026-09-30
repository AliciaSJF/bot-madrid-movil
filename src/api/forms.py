"""Datos de los formularios y su validación, separados de las rutas.

Cada formulario es un dataclass con validate() → mensaje de error o None.
Los valores se devuelven a la plantilla tal cual para no perder lo escrito si hay un error.
"""

import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from src.db import jobs as jobs_repo
from src.db import profiles as profiles_repo


@dataclass
class ProfileForm:
    display_name: str = ""
    color: str = ""
    portal_username: str = ""
    # La contraseña del portal no va aquí: nunca se devuelve al formulario

    def __post_init__(self):
        self.display_name = self.display_name.strip()
        self.portal_username = self.portal_username.strip()

    def validate(self, conn: sqlite3.Connection, portal_password: str) -> str | None:
        if not self.display_name or len(self.display_name) > 20:
            return "El nombre debe tener entre 1 y 20 caracteres."
        if self.color not in profiles_repo.COLORS:
            return "Elige un color de la lista."
        if not self.portal_username or not portal_password:
            return "Faltan el usuario o la contraseña del portal."
        if profiles_repo.name_exists(conn, self.display_name):
            return "Ya hay un perfil con ese nombre."
        return None

    def values(self) -> dict:
        return asdict(self)


@dataclass
class BookingForm:
    """Último paso de "Reservar": el servicio y el centro ya vienen elegidos en la URL."""

    slot_date: str = ""
    slot_time: str = ""
    mode: str = "reservar"
    on_free: str = "reservar"
    dry_run: bool = True

    def slot_at(self, zone: ZoneInfo) -> datetime | None:
        """Fecha y hora del turno con zona horaria, o None si no se pueden leer."""
        try:
            local = datetime.combine(date.fromisoformat(self.slot_date), time.fromisoformat(self.slot_time))
        except ValueError:
            return None
        return local.replace(tzinfo=zone)

    def validate(self, zone: ZoneInfo) -> str | None:
        slot_at = self.slot_at(zone)
        if slot_at is None:
            return "Fecha u hora no válidas."
        if self.mode not in jobs_repo.MODES:
            return "Elige reservar u observar."
        if self.on_free not in jobs_repo.ON_FREE:
            return "Elige qué hacer si se libera una plaza."
        if slot_at <= datetime.now(zone):
            return "Ese turno ya ha pasado."
        return None

    def values(self) -> dict:
        return asdict(self)
