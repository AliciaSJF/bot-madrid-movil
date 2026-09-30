"""Anular desde la app una reserva conseguida por el bot, y avisar por Telegram del resultado."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from src import portal
from src.config import Settings
from src.core.booking import slot_label
from src.core.connection import with_session
from src.db import jobs as jobs_repo
from src.notify import notify

CANCELLABLE = ("reservado", "revisar")


@dataclass(frozen=True)
class CancelOutcome:
    ok: bool
    message: str


def can_cancel(job: jobs_repo.Job, now: datetime) -> bool:
    return job.status in CANCELLABLE and job.slot_at > now


def cancel_booking(conn: sqlite3.Connection, settings: Settings, job_id: int, profile_id: int) -> CancelOutcome:
    """Anula en el portal la reserva del job (solo si es de ese perfil) y avisa por Telegram."""
    job = jobs_repo.get_job(conn, job_id)
    now = datetime.now(settings.zone)
    if job is None or job.profile_id != profile_id:
        return CancelOutcome(False, "Esa reserva no existe.")
    if not can_cancel(job, now):
        return CancelOutcome(False, "Esa reserva no se puede anular (ya pasó, está anulada o no se llegó a hacer).")

    start = job.slot_at.astimezone(settings.zone)
    label = slot_label(settings, job)
    try:
        result = with_session(
            conn, settings, profile_id,
            lambda: portal.cancel_reservation(settings, profile_id, start.date(), start.time(), job.center),
        )
    except portal.PortalError as exc:
        jobs_repo.add_attempt(conn, job.id, "anular", "error", str(exc))
        conn.commit()
        notify(conn, settings, profile_id, f"❌ No se pudo anular\n{label}\n{exc}")
        return CancelOutcome(False, f"No se pudo anular: {exc}")

    jobs_repo.add_attempt(conn, job.id, "anular", result.status, result.message)
    if result.status in ("anulado", "ya_anulado"):
        balance = f"\nSaldo del monedero: {_euros(result.wallet_balance)}" if result.wallet_balance is not None else ""
        jobs_repo.set_status(conn, job.id, "anulado", f"Anulada desde la app. {result.message}")
        conn.commit()
        notify(conn, settings, profile_id, f"🗑️ Reserva anulada\n{label}{balance}")
        return CancelOutcome(True, "Reserva anulada. Te he enviado la confirmación por Telegram.")

    conn.commit()
    notify(conn, settings, profile_id, f"❌ No se pudo anular\n{label}\n{result.message}")
    return CancelOutcome(False, result.message)


def _euros(amount) -> str:
    return f"{amount:.2f} €".replace(".", ",")
