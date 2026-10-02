"""Pantalla "Mis reservas": filtros, orden por cercanía, cancelar, anular y eliminar."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response

from src.api.deps import CurrentProfile, HouseConn, SettingsDep, redirect
from src.api.templating import flash, render
from src.core import booking as core_booking
from src.core import cancel as core_cancel
from src.db import jobs as jobs_repo

router = APIRouter(prefix="/reservas")


@router.get("")
def list_jobs(
    request: Request,
    conn: HouseConn,
    profile: CurrentProfile,
    settings: SettingsDep,
    filtro: Annotated[str, Query()] = jobs_repo.DEFAULT_FILTER,
) -> Response:
    if filtro not in jobs_repo.FILTERS:
        filtro = jobs_repo.DEFAULT_FILTER
    now = datetime.now(settings.zone)
    everything = jobs_repo.list_jobs(conn, profile.id)
    shown = [j for j in everything if jobs_repo.matches_filter(j, filtro, now)]
    upcoming, past = jobs_repo.split_by_date(shown, now)
    return render(
        request,
        "jobs.html",
        profile=profile,
        nav="reservas",
        filters=jobs_repo.FILTERS,
        current_filter=filtro,
        counts={name: sum(jobs_repo.matches_filter(j, name, now) for j in everything) for name in jobs_repo.FILTERS},
        upcoming=upcoming,
        past=past,
        cancellable={j.id for j in shown if core_cancel.can_cancel(j, now)},
        deletable={j.id for j in shown if jobs_repo.is_deletable(j)},
        live=any(_running_now(settings, j, now) for j in shown),
    )


def _running_now(settings, job: jobs_repo.Job, now: datetime) -> bool:
    """¿Está reservando ahora (o a punto)? Entonces la lista se refresca sola hasta ver el resultado."""
    if job.status in ("reservando", "plaza_liberada"):
        return True
    return job.mode == "reservar" and job.status in ("pendiente", "esperando_apertura")         and core_booking.opening_of(settings, job) - core_booking.PREPARE_BEFORE <= now


def _back(filtro: str) -> str:
    return f"/reservas?filtro={filtro}" if filtro in jobs_repo.FILTERS else "/reservas"


@router.post("/{job_id}/cancelar")
def cancel(request: Request, conn: HouseConn, profile: CurrentProfile, job_id: int, filtro: str = "") -> Response:
    """Quita una programación que aún no se ha ejecutado (no toca el portal)."""
    if jobs_repo.cancel_job(conn, job_id, profile.id):
        conn.commit()
        flash(request, "Programación cancelada.")
    return redirect(_back(filtro))


@router.post("/{job_id}/anular")
def annul(
    request: Request, conn: HouseConn, profile: CurrentProfile, settings: SettingsDep, job_id: int, filtro: str = ""
) -> Response:
    """Anula en el portal una reserva ya conseguida. Síncrono: abre el navegador (20–40 s)."""
    outcome = core_cancel.cancel_booking(conn, settings, job_id, profile.id)
    flash(request, outcome.message)
    return redirect(_back(filtro))


@router.post("/{job_id}/eliminar")
def delete(request: Request, conn: HouseConn, profile: CurrentProfile, job_id: int, filtro: str = "") -> Response:
    """Borra de la lista una programación terminada (fallidas, canceladas…). No toca el portal."""
    if jobs_repo.delete_job(conn, job_id, profile.id):
        conn.commit()
        flash(request, "Eliminada de la lista.")
    else:
        flash(request, "Esa programación no se puede eliminar (está en marcha o es una reserva conseguida).")
    return redirect(_back(filtro))

