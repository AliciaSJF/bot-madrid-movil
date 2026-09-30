"""Pantalla "Perfil": datos del perfil elegido y prueba de conexión con el portal."""

from fastapi import APIRouter, Request
from fastapi.responses import Response

from src.api.deps import CurrentProfile, HouseConn, SettingsDep, redirect
from src.api.templating import flash, render
from src.core.connection import check_connection
from src.db import profiles as profiles_repo

router = APIRouter(prefix="/perfil")


@router.get("")
def detail(request: Request, profile: CurrentProfile) -> Response:
    return render(
        request,
        "account.html",
        profile=profile,
        nav="perfil",
        status_label=profiles_repo.PORTAL_STATUS_LABELS[profile.portal_status],
    )


@router.post("/probar")
def test_connection(request: Request, conn: HouseConn, profile: CurrentProfile, settings: SettingsDep) -> Response:
    # Síncrono a propósito: abre el navegador, hace un único intento y lo cierra (10–20 s)
    check = check_connection(conn, settings, profile.id)
    flash(request, "Conexión correcta con el portal." if check.ok else "No se pudo conectar con el portal.")
    return redirect("/perfil")
