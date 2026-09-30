"""Foto guardada de los turnos (horas y plazas) de cada centro y servicio.

Es compartida por todos los perfiles: si dos personas miran el mismo centro, se consulta una vez.
"""

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time


@dataclass(frozen=True)
class StoredSlot:
    day: date
    time: time
    activity: str
    free: int
    total: int
    selectable: bool


def replace_days(
    conn: sqlite3.Connection,
    service: str,
    center_id: int,
    days: Iterable[date],
    slots: Iterable[StoredSlot],
    fetched_at: datetime,
) -> None:
    """Sustituye los turnos de esos días por los recién leídos y apunta cuándo se leyeron."""
    day_keys = [d.isoformat() for d in days]
    conn.executemany(
        "DELETE FROM slots WHERE service = ? AND center_id = ? AND day = ?",
        [(service, center_id, d) for d in day_keys],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO slots (service, center_id, day, time, activity, free, total, selectable) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (service, center_id, s.day.isoformat(), s.time.strftime("%H:%M"), s.activity, s.free, s.total, int(s.selectable))
            for s in slots
        ],
    )
    conn.executemany(
        "INSERT INTO slot_fetches (service, center_id, day, fetched_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(service, center_id, day) DO UPDATE SET fetched_at = excluded.fetched_at",
        [(service, center_id, d, fetched_at.isoformat()) for d in day_keys],
    )


def list_slots(conn: sqlite3.Connection, service: str, center_id: int, days: Iterable[date]) -> list[StoredSlot]:
    day_keys = [d.isoformat() for d in days]
    if not day_keys:
        return []
    marks = ",".join("?" * len(day_keys))
    rows = conn.execute(
        f"SELECT day, time, activity, free, total, selectable FROM slots "
        f"WHERE service = ? AND center_id = ? AND day IN ({marks}) ORDER BY day, time, activity",
        (service, center_id, *day_keys),
    ).fetchall()
    return [
        StoredSlot(
            date.fromisoformat(r["day"]), time.fromisoformat(r["time"]), r["activity"],
            r["free"], r["total"], bool(r["selectable"]),
        )
        for r in rows
    ]


def get_slot(
    conn: sqlite3.Connection, service: str, center_id: int, day: date, at: time, activity: str
) -> StoredSlot | None:
    for slot in list_slots(conn, service, center_id, [day]):
        if slot.time == at and slot.activity == activity:
            return slot
    return None


def oldest_fetch(conn: sqlite3.Connection, service: str, center_id: int, days: Iterable[date]) -> datetime | None:
    """La consulta más antigua entre esos días, o None si falta alguno por consultar."""
    day_keys = [d.isoformat() for d in days]
    marks = ",".join("?" * len(day_keys))
    rows = conn.execute(
        f"SELECT fetched_at FROM slot_fetches WHERE service = ? AND center_id = ? AND day IN ({marks})",
        (service, center_id, *day_keys),
    ).fetchall()
    if len(rows) < len(day_keys):
        return None
    return min(datetime.fromisoformat(r["fetched_at"]) for r in rows)
