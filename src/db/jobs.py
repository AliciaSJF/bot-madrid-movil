"""Reservas programadas (jobs). El worker que las ejecuta llega en hitos posteriores."""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

SERVICES = {"multitrabajo": "Sala multitrabajo", "piscina": "Nado libre"}
MODES = {"reservar": "Reservar en la apertura", "observar": "Observar plazas libres"}
ON_FREE = {"reservar": "Reservar automáticamente", "avisar": "Solo avisarme"}

STATUS_LABELS = {
    "pendiente": "Pendiente",
    "esperando_apertura": "Esperando apertura",
    "reservando": "Reservando",
    "vigilando": "Vigilando",
    "plaza_liberada": "Plaza liberada",
    "reservado": "Reservado",
    "avisado": "Avisado",
    "expirado": "Expirado",
    "fallido": "Fallido",
    "cancelado": "Cancelado",
}
ACTIVE_STATUSES = ("pendiente", "esperando_apertura", "reservando", "vigilando", "plaza_liberada")


@dataclass(frozen=True)
class Job:
    id: int
    profile_id: int
    center: str
    service: str
    slot_at: datetime  # UTC
    mode: str
    on_free: str | None
    dry_run: bool
    status: str

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES


def _from_row(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        profile_id=row["profile_id"],
        center=row["center"],
        service=row["service"],
        slot_at=datetime.fromisoformat(row["slot_at"]),
        mode=row["mode"],
        on_free=row["on_free"],
        dry_run=bool(row["dry_run"]),
        status=row["status"],
    )


def create_job(
    conn: sqlite3.Connection,
    profile_id: int,
    center: str,
    service: str,
    slot_at: datetime,
    mode: str,
    on_free: str | None,
    dry_run: bool,
) -> int:
    if slot_at.tzinfo is None:
        raise ValueError("slot_at debe llevar zona horaria")
    cur = conn.execute(
        "INSERT INTO jobs (profile_id, center, service, slot_at, mode, on_free, dry_run, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'pendiente', ?)",
        (
            profile_id,
            center,
            service,
            slot_at.astimezone(UTC).isoformat(),
            mode,
            on_free if mode == "observar" else None,
            int(dry_run),
            datetime.now(UTC).isoformat(),
        ),
    )
    return cur.lastrowid


def list_jobs(conn: sqlite3.Connection, profile_id: int) -> list[Job]:
    """Activas primero (la más próxima arriba) y después el resto, las más recientes primero."""
    rows = conn.execute("SELECT * FROM jobs WHERE profile_id = ?", (profile_id,)).fetchall()
    jobs = [_from_row(r) for r in rows]
    active = sorted((j for j in jobs if j.is_active), key=lambda j: j.slot_at)
    done = sorted((j for j in jobs if not j.is_active), key=lambda j: j.slot_at, reverse=True)
    return active + done


def recent_centers(conn: sqlite3.Connection, profile_id: int, limit: int = 8) -> list[str]:
    rows = conn.execute(
        "SELECT center FROM jobs WHERE profile_id = ? GROUP BY center "
        "ORDER BY MAX(created_at) DESC LIMIT ?",
        (profile_id, limit),
    ).fetchall()
    return [r["center"] for r in rows]


def cancel_job(conn: sqlite3.Connection, job_id: int, profile_id: int) -> bool:
    """Cancela solo si el job es de ese perfil y sigue activo."""
    placeholders = ",".join("?" * len(ACTIVE_STATUSES))
    cur = conn.execute(
        f"UPDATE jobs SET status = 'cancelado' "
        f"WHERE id = ? AND profile_id = ? AND status IN ({placeholders})",
        (job_id, profile_id, *ACTIVE_STATUSES),
    )
    return cur.rowcount == 1
