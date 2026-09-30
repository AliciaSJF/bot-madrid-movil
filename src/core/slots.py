"""Turnos de un centro: consultarlos en el portal, guardarlos y decidir su estado.

Estado de cada turno (lo que ve la usuaria):
    libre      ya abierto y con plazas → reservar
    completo   ya abierto y sin plazas → observar (opción principal)
    sin_abrir  abre 49 h antes del inicio → reservar en la apertura
    pasado     ya ha empezado
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from src import portal
from src.config import Settings
from src.core.connection import with_session
from src.db import slots as slots_repo

# Reglas del portal (madrid.es). Observado el 2026-09-30: los turnos a menos de 49 h ya tenían
# reservas y los de más de 49 h tenían libres = aforo.
BOOKING_WINDOW = timedelta(hours=49)
WEEK_DAYS = 7
# No preguntar al portal por el mismo centro más de una vez por minuto (compartido entre perfiles)
MIN_REFRESH_INTERVAL = timedelta(seconds=60)
# A partir de aquí la foto se considera vieja y la página pide actualizarla sola
STALE_AFTER = timedelta(minutes=10)


@dataclass(frozen=True)
class SlotView:
    day: date
    time: time
    activity: str
    free: int
    total: int
    status: str
    opens_at: datetime  # en la zona de la app

    @property
    def key(self) -> str:
        return f"{self.day.isoformat()}T{self.time:%H:%M}"


def slot_start(day: date, at: time, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, at, tzinfo=zone)


def opening_time(start: datetime) -> datetime:
    """Apertura = inicio − 49 h de tiempo real. Se resta en UTC para que el cambio de hora no descuadre.

    SIN VERIFICAR en días de cambio de hora: el portal podría contar 49 h de reloj de pared.
    """
    return (start.astimezone(UTC) - BOOKING_WINDOW).astimezone(start.tzinfo)


def classify(slot: slots_repo.StoredSlot, now: datetime, zone: ZoneInfo) -> SlotView:
    start = slot_start(slot.day, slot.time, zone)
    opens_at = opening_time(start)
    if start <= now:
        status = "pasado"
    elif now < opens_at:
        status = "sin_abrir"
    elif slot.selectable and slot.free > 0:
        status = "libre"
    else:
        status = "completo"
    return SlotView(slot.day, slot.time, slot.activity, slot.free, slot.total, status, opens_at)


def week(today: date) -> list[date]:
    return [today + timedelta(days=i) for i in range(WEEK_DAYS)]


def get_slots(
    conn: sqlite3.Connection, settings: Settings, service: str, center_id: int, days: list[date], now: datetime
) -> list[SlotView]:
    stored = slots_repo.list_slots(conn, service, center_id, days)
    return [classify(s, now, settings.zone) for s in stored]


def needs_refresh(conn: sqlite3.Connection, service: str, center_id: int, days: list[date], now: datetime) -> bool:
    oldest = slots_repo.oldest_fetch(conn, service, center_id, days)
    return oldest is None or now - oldest > STALE_AFTER


def refresh_slots(
    conn: sqlite3.Connection,
    settings: Settings,
    profile_id: int,
    service: str,
    center_id: int,
    days: list[date],
    now: datetime,
) -> bool:
    """Consulta el portal y guarda la foto. Devuelve False si se omitió por haberse consultado hace poco."""
    oldest = slots_repo.oldest_fetch(conn, service, center_id, days)
    if oldest is not None and now - oldest < MIN_REFRESH_INTERVAL:
        return False
    found = with_session(
        conn, settings, profile_id, lambda: portal.fetch_slots(settings, profile_id, service, center_id, days)
    )
    slots_repo.replace_days(
        conn,
        service,
        center_id,
        days,
        (slots_repo.StoredSlot(s.day, s.time, s.activity, s.free, s.total, s.selectable) for s in found),
        now,
    )
    conn.commit()
    return True
