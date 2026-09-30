"""Pantalla "Perfil": datos del perfil elegido, conexión con el portal y avisos por Telegram."""

from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from src.api.deps import CurrentProfile, HouseConn, SettingsDep, redirect
from src.api.templating import flash, render
from src.core.connection import check_connection
from src.core.prefetch import schedule_prefetch
from src.db import profiles as profiles_repo
from src.notify import linking, notify
from src.notify.telegram import TelegramError
from src.worker.scheduler import current_worker

router = APIRouter(prefix="/perfil")

TELEGRAM_LINK_SESSION_KEY = "telegram_link"


@router.get("")
def detail(request: Request, conn: HouseConn, profile: CurrentProfile, settings: SettingsDep) -> Response:
    now = datetime.now(settings.zone)
    pending = not profile.telegram_linked and profiles_repo.has_pending_link(conn, profile.id, now)
    return render(
        request,
        "account.html",
        profile=profile,
        nav="perfil",
        status_label=profiles_repo.PORTAL_STATUS_LABELS[profile.portal_status],
        telegram_configured=settings.telegram_bot_token is not None,
        telegram_link=request.session.get(TELEGRAM_LINK_SESSION_KEY) if pending else None,
    )


@router.post("/probar")
def test_connection(request: Request, conn: HouseConn, profile: CurrentProfile, settings: SettingsDep) -> Response:
    # Síncrono a propósito: abre el navegador, hace un único intento y lo cierra (10–20 s)
    check = check_connection(conn, settings, profile.id)
    if check.ok:
        schedule_prefetch(settings, profile.id)
    flash(request, "Conexión correcta con el portal." if check.ok else "No se pudo conectar con el portal.")
    return redirect("/perfil")


# --- Telegram ------------------------------------------------------------------

@router.post("/telegram/vincular")
def telegram_start_link(request: Request, conn: HouseConn, profile: CurrentProfile, settings: SettingsDep) -> Response:
    try:
        link = linking.start_link(conn, settings, profile.id, datetime.now(settings.zone))
    except TelegramError as exc:
        flash(request, f"No se pudo preparar Telegram: {exc}")
        return redirect("/perfil")
    request.session[TELEGRAM_LINK_SESSION_KEY] = link
    return redirect("/perfil")


@router.post("/telegram/comprobar")
def telegram_check(request: Request, conn: HouseConn, profile: CurrentProfile, settings: SettingsDep) -> Response:
    """La página lo llama cada pocos segundos mientras espera a que se pulse "Iniciar" en Telegram."""
    worker = current_worker()
    if worker is None or not worker.telegram_listening:
        # Sin el hilo de Telegram (p. ej. arrancado sin token): se leen aquí los mensajes
        try:
            linking.process_updates(conn, settings, datetime.now(settings.zone))
        except TelegramError as exc:
            return JSONResponse({"linked": False, "error": str(exc)})
    linked = profiles_repo.get_profile(conn, profile.id).telegram_linked
    if linked:
        request.session.pop(TELEGRAM_LINK_SESSION_KEY, None)
        flash(request, "Telegram vinculado. Te hemos enviado un mensaje de confirmación.")
    return JSONResponse({"linked": linked})


@router.post("/telegram/prueba")
def telegram_test(request: Request, conn: HouseConn, profile: CurrentProfile, settings: SettingsDep) -> Response:
    sent = notify(conn, settings, profile.id, f"🔔 Mensaje de prueba para «{profile.display_name}». ¡Los avisos funcionan!")
    flash(request, "Mensaje de prueba enviado." if sent else "No se pudo enviar el mensaje de prueba.")
    return redirect("/perfil")


@router.post("/telegram/desvincular")
def telegram_unlink(request: Request, conn: HouseConn, profile: CurrentProfile) -> Response:
    profiles_repo.set_telegram_chat(conn, profile.id, None)
    conn.commit()
    request.session.pop(TELEGRAM_LINK_SESSION_KEY, None)
    flash(request, "Telegram desvinculado: este perfil ya no recibirá avisos.")
    return redirect("/perfil")
