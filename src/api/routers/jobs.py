"""Pantalla "Mis reservas": lista de programaciones del perfil y cancelar."""

from fastapi import APIRouter, Request
from fastapi.responses import Response

from src.api.deps import CurrentProfile, HouseConn, redirect
from src.api.templating import flash, render
from src.db import jobs as jobs_repo

router = APIRouter(prefix="/reservas")


@router.get("")
def list_jobs(request: Request, conn: HouseConn, profile: CurrentProfile) -> Response:
    return render(
        request,
        "jobs.html",
        profile=profile,
        nav="reservas",
        jobs=jobs_repo.list_jobs(conn, profile.id),
    )


@router.post("/{job_id}/cancelar")
def cancel(request: Request, conn: HouseConn, profile: CurrentProfile, job_id: int) -> Response:
    # Solo cancela si la programación es de este perfil y sigue activa
    if jobs_repo.cancel_job(conn, job_id, profile.id):
        conn.commit()
        flash(request, "Programación cancelada.")
    return redirect("/reservas")
