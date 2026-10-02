"""Pantalla "Reservar", en cuatro pasos: servicio → polideportivo → turno → confirmar.

    /reservar                                   1. elegir servicio
    /reservar/{service}                         2. elegir polideportivo (favoritos arriba)
    /reservar/{service}/actualizar                 traer la lista de polideportivos del portal
    /reservar/{service}/{center_id}?dia=…       3. semana y turnos con plazas libres
    /reservar/{service}/{center_id}/huecos         traer los turnos de la semana del portal
    /reservar/{service}/{center_id}/programar   4. reservar u observar ese turno
"""

from datetime import date, datetime, time
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import Response

from src import portal
from src.api.deps import CurrentProfile, HouseConn, SettingsDep, redirect
from src.api.forms import BookingForm
from src.api.templating import flash, render
from src.core import booking as core_booking
from src.core import slots as core_slots
from src.core.centers import refresh_centers
from src.core.prefetch import schedule_prefetch
from src.db import centers as centers_repo
from src.db import jobs as jobs_repo
from src.db import slots as slots_repo
from src.worker.scheduler import current_worker

router = APIRouter(prefix="/reservar")


class Service(StrEnum):
    multitrabajo = "multitrabajo"
    piscina = "piscina"


# --- 1: servicio -------------------------------------------------------------

@router.get("")
def pick_service(request: Request, profile: CurrentProfile, settings: SettingsDep) -> Response:
    # Mientras eliges servicio y centro, se van trayendo los huecos de tus favoritos
    if profile.portal_status == "ok":
        schedule_prefetch(settings, profile.id)
    return render(request, "book_service.html", profile=profile, nav="reservar")


# --- 2: polideportivo --------------------------------------------------------

@router.get("/{service}")
def pick_center(request: Request, conn: HouseConn, profile: CurrentProfile, service: Service) -> Response:
    return render(
        request,
        "book_center.html",
        profile=profile,
        nav="reservar",
        service=service.value,
        centers=centers_repo.list_centers(conn, service, profile.id),
        updated_at=centers_repo.last_updated(conn, service),
    )


@router.post("/{service}/actualizar")
def refresh_center_list(
    request: Request, conn: HouseConn, profile: CurrentProfile, settings: SettingsDep, service: Service
) -> Response:
    # Síncrono a propósito: abre el navegador, lee la lista y lo cierra (10–20 s)
    try:
        count = refresh_centers(conn, settings, profile.id, service)
        flash(request, f"Lista actualizada: {count} polideportivos.")
    except portal.PortalError as exc:
        flash(request, f"No se pudo actualizar: {exc}")
    return redirect(f"/reservar/{service}")


# --- 3: semana y turnos ------------------------------------------------------

@router.get("/{service}/{center_id:int}")
def pick_slot(
    request: Request,
    conn: HouseConn,
    profile: CurrentProfile,
    settings: SettingsDep,
    service: Service,
    center_id: int,
    dia: Annotated[date | None, Query()] = None,
) -> Response:
    center = centers_repo.get_center(conn, center_id, service)
    if center is None:
        return redirect(f"/reservar/{service}")

    now = datetime.now(settings.zone)
    days = core_slots.week(now.date())
    selected = dia if dia in days else days[0]
    slots = core_slots.get_slots(conn, settings, service, center_id, days, now)
    return render(
        request,
        "book_slots.html",
        profile=profile,
        nav="reservar",
        service=service.value,
        center=center,
        days=days,
        selected=selected,
        day_slots=[s for s in slots if s.day == selected],
        free_by_day={d: sum(1 for s in slots if s.day == d and s.status == "libre") for d in days},
        has_data_by_day={d: any(s.day == d for s in slots) for d in days},
        fetched_at=slots_repo.oldest_fetch(conn, service, center_id, days),
        stale=core_slots.needs_refresh(conn, service, center_id, days, now),
    )


@router.post("/{service}/{center_id:int}/huecos")
def refresh_slots(
    request: Request,
    conn: HouseConn,
    profile: CurrentProfile,
    settings: SettingsDep,
    service: Service,
    center_id: int,
    dia: Annotated[str, Form()] = "",
) -> Response:
    back = f"/reservar/{service}/{center_id}" + (f"?dia={dia}" if dia else "")
    if centers_repo.get_center(conn, center_id, service) is None:
        return redirect(f"/reservar/{service}")
    now = datetime.now(settings.zone)
    try:
        core_slots.refresh_slots(conn, settings, profile.id, service, center_id, core_slots.week(now.date()), now)
    except portal.PortalError as exc:
        flash(request, f"No se pudieron consultar los turnos: {exc}")
    # Si la página lo pidió sola con fetch(), le basta con saber que ha terminado
    if request.headers.get("x-requested-with") == "fetch":
        return Response(status_code=204)
    return redirect(back)


# --- 4: reservar u observar el turno -----------------------------------------

@router.get("/{service}/{center_id:int}/programar")
def confirm_form(
    request: Request,
    conn: HouseConn,
    profile: CurrentProfile,
    settings: SettingsDep,
    service: Service,
    center_id: int,
    dia: Annotated[date, Query()],
    hora: Annotated[time, Query()],
    actividad: Annotated[str, Query()] = "",
) -> Response:
    center = centers_repo.get_center(conn, center_id, service)
    slot = _slot_view(conn, settings, service, center_id, dia, hora, actividad)
    if center is None or slot is None:
        return redirect(f"/reservar/{service}/{center_id}?dia={dia.isoformat()}")
    # Turno completo → Observar es la opción principal; en otro caso, reservar
    mode = "observar" if slot.status == "completo" else "reservar"
    form = BookingForm(slot_date=dia.isoformat(), slot_time=f"{hora:%H:%M}", mode=mode)
    return _render_confirm(request, profile, settings, service, center, slot, form)


@router.post("/{service}/{center_id:int}/programar")
def confirm_submit(
    request: Request,
    conn: HouseConn,
    profile: CurrentProfile,
    settings: SettingsDep,
    service: Service,
    center_id: int,
    slot_date: Annotated[str, Form()],
    slot_time: Annotated[str, Form()],
    mode: Annotated[str, Form()],
    activity: Annotated[str, Form()] = "",
    on_free: Annotated[str, Form()] = "reservar",
) -> Response:
    center = centers_repo.get_center(conn, center_id, service)
    if center is None:
        return redirect(f"/reservar/{service}")
    form = BookingForm(slot_date, slot_time, mode, on_free)

    error = form.validate(settings.zone)
    slot_at = form.slot_at(settings.zone)
    slot = _slot_view(conn, settings, service, center_id, slot_at.date(), slot_at.time(), activity) if slot_at else None
    if error or slot is None:
        if slot is None:
            flash(request, error or "Ese turno ya no aparece: elige otro.")
            return redirect(f"/reservar/{service}/{center_id}")
        return _render_confirm(request, profile, settings, service, center, slot, form, error, 400)

    job_id = jobs_repo.create_job(
        conn,
        profile_id=profile.id,
        center_id=center.portal_id,
        center=center.name,
        activity=slot.activity,
        service=service,
        slot_at=slot_at,
        mode=form.mode,
        on_free=form.on_free,
    )
    job = jobs_repo.get_job(conn, job_id)
    if form.mode == "observar":
        message = "Vigilando el turno: te aviso por Telegram si se libera una plaza."
    elif slot.status == "sin_abrir":
        jobs_repo.set_status(conn, job_id, "esperando_apertura", core_booking.planned_message(settings, job))
        opens = slot.opens_at
        message = (f"Reserva planificada para el {core_booking.short_day(opens)} a las {opens:%H:%M}, cuando abra "
                   "el turno. Te aviso por Telegram con el resultado.")
    else:
        message = "Reservando ahora: en unos segundos verás aquí si se ha confirmado (y te llegará por Telegram)."
    conn.commit()
    worker = current_worker()
    if form.mode == "reservar" and slot.status != "sin_abrir" and worker is not None:
        worker.start_booking(job_id)  # ya está abierto: sin esperar a la siguiente vuelta del programador
    flash(request, message)
    return redirect("/reservas")


def _slot_view(
    conn, settings, service: str, center_id: int, day: date, at: time, activity: str
) -> core_slots.SlotView | None:
    stored = slots_repo.get_slot(conn, service, center_id, day, at, activity)
    if stored is None:
        return None
    return core_slots.classify(stored, datetime.now(settings.zone), settings.zone)


def _render_confirm(
    request, profile, settings, service, center, slot, form: BookingForm, error: str | None = None, status_code=200
) -> Response:
    return render(
        request,
        "book.html",
        status_code,
        profile=profile,
        nav="reservar",
        service=service,
        center=center,
        slot=slot,
        error=error,
        values=form.values(),
    )
