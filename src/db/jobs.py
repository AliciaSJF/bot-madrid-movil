"""Reservas programadas (jobs) e historial de intentos."""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

SERVICES = {"multitrabajo": "Sala multitrabajo", "piscina": "Nado libre"}
MODES = {"reservar": "Reservar", "observar": "Observar plazas libres"}
ON_FREE = {"reservar": "Reservar automáticamente", "avisar": "Solo avisarme"}

STATUS_LABELS = {
    "pendiente": "Planificada",
    "esperando_apertura": "Planificada",
    "reservando": "Reservando…",
    "vigilando": "Vigilando",
    "plaza_liberada": "Plaza liberada",
    "reservado": "Reserva confirmada",
    "prueba_ok": "Prueba superada",
    "avisado": "Avisado",
    "revisar": "Revisar en el portal",
    "anulado": "Anulada",
    "expirado": "Expirado",
    "fallido": "Fallido",
    "cancelado": "Cancelado",
}
ACTIVE_STATUSES = ("pendiente", "esperando_apertura", "reservando", "vigilando", "plaza_liberada")


@dataclass(frozen=True)
class Job:
    id: int
    profile_id: int
    center: str  # nombre del polideportivo en el momento de programar
    center_id: int | None  # facility_code del portal
    activity: str  # p. ej. "Nado libre · Calle central"; vacío en jobs antiguos
    service: str
    slot_at: datetime  # UTC
    mode: str
    on_free: str | None
    dry_run: bool  # solo jobs antiguos: el modo prueba ya no existe
    status: str
    result_message: str | None = None

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES


@dataclass(frozen=True)
class Attempt:
    at: datetime
    action: str
    result: str
    message: str


def _from_row(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        profile_id=row["profile_id"],
        center=row["center"],
        center_id=row["center_id"],
        activity=row["activity"],
        service=row["service"],
        slot_at=datetime.fromisoformat(row["slot_at"]),
        mode=row["mode"],
        on_free=row["on_free"],
        dry_run=bool(row["dry_run"]),
        status=row["status"],
        result_message=row["result_message"],
    )


def create_job(
    conn: sqlite3.Connection,
    profile_id: int,
    center_id: int,
    center: str,
    activity: str,
    service: str,
    slot_at: datetime,
    mode: str,
    on_free: str | None,
    source_job_id: int | None = None,
) -> int:
    if slot_at.tzinfo is None:
        raise ValueError("slot_at debe llevar zona horaria")
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        "INSERT INTO jobs (profile_id, center_id, center, activity, service, slot_at, mode, on_free, dry_run, "
        "status, created_at, updated_at, source_job_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendiente', ?, ?, ?)",
        (
            profile_id,
            center_id,
            center,
            activity,
            service,
            slot_at.astimezone(UTC).isoformat(),
            mode,
            on_free if mode == "observar" else None,
            0,  # dry_run: el modo prueba ya no existe (la columna queda para el historial)
            now,
            now,
            source_job_id,
        ),
    )
    return cur.lastrowid


def create_watch_from(conn: sqlite3.Connection, job: Job, on_free: str = "reservar") -> int:
    """Vigilancia del mismo turno que una reserva que no salió (botón «Observar» de Telegram)."""
    existing = conn.execute(
        "SELECT id FROM jobs WHERE source_job_id = ? AND mode = 'observar'", (job.id,)
    ).fetchone()
    if existing:
        return existing["id"]
    return create_job(
        conn, job.profile_id, job.center_id, job.center, job.activity, job.service,
        job.slot_at, "observar", on_free, source_job_id=job.id,
    )


def get_job(conn: sqlite3.Connection, job_id: int) -> Job | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _from_row(row) if row else None


def list_jobs(conn: sqlite3.Connection, profile_id: int) -> list[Job]:
    """Activas primero (la más próxima arriba) y después el resto, las más recientes primero."""
    rows = conn.execute("SELECT * FROM jobs WHERE profile_id = ?", (profile_id,)).fetchall()
    jobs = [_from_row(r) for r in rows]
    active = sorted((j for j in jobs if j.is_active), key=lambda j: j.slot_at)
    done = sorted((j for j in jobs if not j.is_active), key=lambda j: j.slot_at, reverse=True)
    return active + done


def list_active(conn: sqlite3.Connection) -> list[Job]:
    """Todas las programaciones activas de todos los perfiles, la más próxima primero."""
    marks = ",".join("?" * len(ACTIVE_STATUSES))
    rows = conn.execute(
        f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY slot_at, id", ACTIVE_STATUSES
    ).fetchall()
    return [_from_row(r) for r in rows]


def set_status(
    conn: sqlite3.Connection,
    job_id: int,
    status: str,
    message: str | None = None,
    only_if: tuple[str, ...] | None = None,
) -> bool:
    """Cambia el estado. Con only_if, solo si el estado actual es uno de esos (evita pisar una cancelación)."""
    if status not in STATUS_LABELS:
        raise ValueError(f"Estado desconocido: {status}")
    query = "UPDATE jobs SET status = ?, result_message = COALESCE(?, result_message), updated_at = ? WHERE id = ?"
    params: list = [status, message, datetime.now(UTC).isoformat(), job_id]
    if only_if:
        query += f" AND status IN ({','.join('?' * len(only_if))})"
        params.extend(only_if)
    return conn.execute(query, params).rowcount == 1


def cancel_job(conn: sqlite3.Connection, job_id: int, profile_id: int) -> bool:
    """Cancela solo si el job es de ese perfil y sigue activo."""
    placeholders = ",".join("?" * len(ACTIVE_STATUSES))
    cur = conn.execute(
        f"UPDATE jobs SET status = 'cancelado', updated_at = ? "
        f"WHERE id = ? AND profile_id = ? AND status IN ({placeholders})",
        (datetime.now(UTC).isoformat(), job_id, profile_id, *ACTIVE_STATUSES),
    )
    return cur.rowcount == 1


def add_attempt(conn: sqlite3.Connection, job_id: int, action: str, result: str, message: str = "") -> None:
    """Historial. El mensaje nunca lleva contraseñas, cookies ni tokens."""
    conn.execute(
        "INSERT INTO attempts (job_id, at, action, result, message) VALUES (?, ?, ?, ?, ?)",
        (job_id, datetime.now(UTC).isoformat(timespec="milliseconds"), action, result, message[:300]),
    )


def list_attempts(conn: sqlite3.Connection, job_id: int) -> list[Attempt]:
    rows = conn.execute(
        "SELECT at, action, result, message FROM attempts WHERE job_id = ? ORDER BY id", (job_id,)
    ).fetchall()
    return [Attempt(datetime.fromisoformat(r["at"]), r["action"], r["result"], r["message"]) for r in rows]


# --- «Mis reservas»: filtros, orden y borrado -------------------------------------

FILTERS = {
    "proximas": "Próximas",
    "programadas": "Programadas",
    "conseguidas": "Conseguidas",
    "fallidas": "Fallidas",
    "anuladas": "Anuladas",
    "todas": "Todas",
}
DEFAULT_FILTER = "proximas"

# Se pueden borrar las programaciones terminadas que no son el registro de un pago real.
# Las conseguidas («reservado», «revisar») no se borran; las que están en marcha se cancelan antes.
DELETABLE_STATUSES = ("prueba_ok", "fallido", "cancelado", "expirado", "anulado", "avisado")


def is_deletable(job: Job) -> bool:
    return not job.is_active and job.status in DELETABLE_STATUSES


def matches_filter(job: Job, name: str, now: datetime) -> bool:
    if name == "proximas":
        return job.slot_at > now
    if name == "programadas":
        return job.is_active
    if name == "conseguidas":
        return job.status in ("reservado", "revisar")
    if name == "fallidas":
        return job.status in ("fallido", "expirado")
    if name == "anuladas":
        return job.status in ("anulado", "cancelado")
    return True  # «todas»


def split_by_date(jobs: list[Job], now: datetime) -> tuple[list[Job], list[Job]]:
    """(próximas, pasadas): las próximas de la más cercana a la más lejana; las pasadas de la más
    reciente a la más antigua. Así lo primero que se ve es siempre lo más cercano a hoy."""
    upcoming = sorted((j for j in jobs if j.slot_at > now), key=lambda j: (j.slot_at, j.id))
    past = sorted((j for j in jobs if j.slot_at <= now), key=lambda j: (j.slot_at, j.id), reverse=True)
    return upcoming, past


def delete_job(conn: sqlite3.Connection, job_id: int, profile_id: int) -> bool:
    """Borra una programación terminada del perfil (y su historial). No borra reservas conseguidas."""
    job = get_job(conn, job_id)
    if job is None or job.profile_id != profile_id or not is_deletable(job):
        return False
    # Las vigilancias creadas desde esta programación se quedan, sin el enlace
    conn.execute("UPDATE jobs SET source_job_id = NULL WHERE source_job_id = ?", (job_id,))
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))  # attempts: ON DELETE CASCADE
    return True

