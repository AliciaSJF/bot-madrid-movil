"""Dependencias comunes de los routers: ajustes, conexión a la BD y control de acceso.

Cadena de acceso: get_conn → require_house (contraseña de casa) → require_profile (perfil elegido).
Si falta un paso se lanza Redirect y el usuario va a la pantalla que corresponde.
"""

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse

from src.api import auth
from src.config import Settings
from src.db import profiles as profiles_repo
from src.db.database import connect


class Redirect(Exception):
    """Se lanza desde una dependencia para mandar a otra pantalla (se maneja en app.py)."""

    def __init__(self, url: str):
        self.url = url


def redirect(url: str) -> RedirectResponse:
    # 303: tras un POST el navegador hace GET a la nueva URL
    return RedirectResponse(url, status_code=303)


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(get_app_settings)]


def get_conn(settings: SettingsDep) -> Iterator[sqlite3.Connection]:
    conn = connect(settings.data_dir)
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def require_house(request: Request, conn: Conn) -> sqlite3.Connection:
    if not auth.house_password_is_set(conn):
        raise Redirect("/configurar")
    if not auth.has_house_session(request.session):
        raise Redirect("/entrar")
    return conn


HouseConn = Annotated[sqlite3.Connection, Depends(require_house)]


def require_profile(request: Request, conn: HouseConn) -> profiles_repo.Profile:
    profile_id = auth.current_profile_id(request.session)
    profile = profiles_repo.get_profile(conn, profile_id) if profile_id else None
    if profile is None:
        request.session.pop("profile_id", None)
        raise Redirect("/perfiles")
    return profile


CurrentProfile = Annotated[profiles_repo.Profile, Depends(require_profile)]
