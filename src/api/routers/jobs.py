"""Pantalla "Mis reservas": programaciones del perfil, cancelar una programación y anular una reserva."""

from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import Response

from src.api.deps import CurrentProfile, HouseConn, SettingsDep, redirect
from src.api.templating import flash, render
from src.core import cancel as core_cancel
from src.db import jobs as jobs_repo

router = APIRouter(prefix="/reservas")


@router.get("")
def list_jobs(request: Request, conn: HouseConn, profile: CurrentProfile, settings: SettingsDep) -> Response:
    now = datetime.now(settings.zone)
    jobs = jobs_repo.list_jobs(conn, profile.id)
    return render(
        request,
        "jobs.html",
        profile=profile,
        nav="reservas",
        jobs=jobs,
        cancellable={j.id for j in jobs if core_cancel.can_cancel(j, now)},
    )


@router.post("/{job_id}/cancelar")
def cancel(request: Request, conn: HouseConn, profile: CurrentProfile, job_id: int) -> Response:
    """Quita una programación que aún no se ha ejecutado (no toca el portal)."""
    if jobs_repo.cancel_job(conn, job_id, profile.id):
        conn.commit()
        flash(request, "Programación cancelada.")
    return redirect("/reservas")


@router.post("/{job_id}/anular")
def annul(request: Request, conn: HouseConn, profile: CurrentProfile, settings: SettingsDep, job_id: int) -> Response:
    """Anula en el portal una reserva ya conseguida. Síncrono: abre el navegador (20–40 s)."""
    outcome = core_cancel.cancel_booking(conn, settings, job_id, profile.id)
    flash(request, outcome.message)
    return redirect("/reservas")
