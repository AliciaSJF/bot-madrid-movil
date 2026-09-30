"""Perfiles estilo Netflix: elegir quién eres y crear un perfil con su cuenta del portal."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

from src.api import auth
from src.api.deps import HouseConn, SettingsDep, redirect
from src.api.forms import ProfileForm
from src.api.templating import flash, render
from src.config import Settings
from src.db import crypto
from src.db import profiles as profiles_repo

router = APIRouter(prefix="/perfiles")


@router.get("")
def picker(request: Request, conn: HouseConn) -> Response:
    return render(
        request,
        "profiles.html",
        profiles=profiles_repo.list_profiles(conn),
        current_id=auth.current_profile_id(request.session),
    )


@router.post("/{profile_id}/elegir")
def choose(request: Request, conn: HouseConn, profile_id: int) -> Response:
    if profiles_repo.get_profile(conn, profile_id) is None:
        return redirect("/perfiles")
    auth.set_current_profile(request.session, profile_id)
    return redirect("/reservas")


@router.get("/nuevo")
def new_form(request: Request, conn: HouseConn, settings: SettingsDep) -> Response:
    form = ProfileForm(color=_first_free_color(conn))
    return _render_form(request, settings, form)


@router.post("/nuevo")
def new_submit(
    request: Request,
    conn: HouseConn,
    settings: SettingsDep,
    display_name: Annotated[str, Form()],
    color: Annotated[str, Form()],
    portal_username: Annotated[str, Form()],
    portal_password: Annotated[str, Form()],
) -> Response:
    form = ProfileForm(display_name=display_name, color=color, portal_username=portal_username)

    error = form.validate(conn, portal_password)
    if error:
        return _render_form(request, settings, form, error, 400)

    try:
        password_enc = crypto.encrypt(settings, portal_password)
    except crypto.MissingKeyError:
        return _render_form(request, settings, form, "Falta FERNET_KEY en el servidor: no se puede guardar.", 500)

    profile_id = profiles_repo.create_profile(
        conn, form.display_name, form.color, form.portal_username, password_enc
    )
    conn.commit()
    auth.set_current_profile(request.session, profile_id)
    flash(request, f"Perfil «{form.display_name}» creado.")
    return redirect("/reservas")


def _first_free_color(conn: sqlite3.Connection) -> str:
    used = {p.color for p in profiles_repo.list_profiles(conn)}
    return next((c for c in profiles_repo.COLORS if c not in used), profiles_repo.COLORS[0])


def _render_form(
    request: Request, settings: Settings, form: ProfileForm, error: str | None = None, status_code: int = 200
) -> Response:
    return render(
        request,
        "profile_new.html",
        status_code,
        colors=profiles_repo.COLORS,
        key_missing=settings.fernet_key is None,
        error=error,
        values=form.values(),
    )
