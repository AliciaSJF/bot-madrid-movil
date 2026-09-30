"""Pantalla "Reservar": programar una reserva o una vigilancia."""

import sqlite3
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

from src.api.deps import CurrentProfile, HouseConn, SettingsDep, redirect
from src.api.forms import BookingForm
from src.api.templating import flash, render
from src.config import Settings
from src.db import jobs as jobs_repo
from src.db.profiles import Profile

router = APIRouter(prefix="/reservar")


@router.get("")
def book_form(request: Request, conn: HouseConn, profile: CurrentProfile, settings: SettingsDep) -> Response:
    tomorrow = datetime.now(settings.zone).date() + timedelta(days=1)
    form = BookingForm(slot_date=tomorrow.isoformat())
    return _render_form(request, conn, profile, settings, form)


@router.post("")
def book_submit(
    request: Request,
    conn: HouseConn,
    profile: CurrentProfile,
    settings: SettingsDep,
    center: Annotated[str, Form()],
    service: Annotated[str, Form()],
    slot_date: Annotated[str, Form()],
    slot_time: Annotated[str, Form()],
    mode: Annotated[str, Form()],
    on_free: Annotated[str, Form()] = "reservar",
    dry_run: Annotated[bool, Form()] = False,
) -> Response:
    form = BookingForm(center, service, slot_date, slot_time, mode, on_free, dry_run)

    error = form.validate(settings.zone)
    if error:
        return _render_form(request, conn, profile, settings, form, error, 400)

    jobs_repo.create_job(
        conn,
        profile_id=profile.id,
        center=form.center,
        service=form.service,
        slot_at=form.slot_at(settings.zone),
        mode=form.mode,
        on_free=form.on_free,
        # Con DRY_RUN=true en el servidor nunca se programa una reserva real
        dry_run=form.dry_run or settings.dry_run,
    )
    conn.commit()
    flash(request, "Reserva programada.")
    return redirect("/reservas")


def _render_form(
    request: Request,
    conn: sqlite3.Connection,
    profile: Profile,
    settings: Settings,
    form: BookingForm,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    return render(
        request,
        "book.html",
        status_code,
        profile=profile,
        nav="reservar",
        centers=jobs_repo.recent_centers(conn, profile.id),
        forced_dry_run=settings.dry_run,
        error=error,
        values=form.values(),
    )
