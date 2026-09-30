"""Cuenta de casa: crear la contraseña la primera vez, entrar y salir."""

import time
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

from src.api import auth
from src.api.deps import Conn, CurrentProfile, redirect
from src.api.templating import render

router = APIRouter()

FAILED_LOGIN_DELAY_S = 1  # frena intentos seguidos


@router.get("/")
def home(_profile: CurrentProfile) -> Response:
    return redirect("/reservas")


@router.get("/configurar")
def setup_form(request: Request, conn: Conn) -> Response:
    if auth.house_password_is_set(conn):
        return redirect("/entrar")
    return render(request, "setup.html", min_length=auth.MIN_HOUSE_PASSWORD)


@router.post("/configurar")
def setup_submit(
    request: Request,
    conn: Conn,
    password: Annotated[str, Form()],
    password2: Annotated[str, Form()],
) -> Response:
    # Solo se puede configurar una vez: después, nadie puede sobrescribirla desde la web
    if auth.house_password_is_set(conn):
        return redirect("/entrar")

    error = auth.validate_new_house_password(password, password2)
    if error:
        return render(request, "setup.html", 400, error=error, min_length=auth.MIN_HOUSE_PASSWORD)

    auth.set_house_password(conn, password)
    conn.commit()
    auth.start_house_session(request.session)
    return redirect("/perfiles")


@router.get("/entrar")
def login_form(request: Request, conn: Conn) -> Response:
    if not auth.house_password_is_set(conn):
        return redirect("/configurar")
    if auth.has_house_session(request.session):
        return redirect("/perfiles")
    return render(request, "login.html")


@router.post("/entrar")
def login_submit(request: Request, conn: Conn, password: Annotated[str, Form()]) -> Response:
    if not auth.house_password_is_set(conn):
        return redirect("/configurar")
    if not auth.check_house_password(conn, password):
        time.sleep(FAILED_LOGIN_DELAY_S)
        return render(request, "login.html", 401, error="Contraseña incorrecta.")

    auth.start_house_session(request.session)
    return redirect("/perfiles")


@router.post("/salir")
def logout(request: Request) -> Response:
    request.session.clear()
    return redirect("/entrar")
