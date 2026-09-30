"""Traer del portal la lista de polideportivos de un servicio y guardarla."""

import sqlite3

from src import portal
from src.config import Settings
from src.core.connection import with_session
from src.db import centers as centers_repo


def refresh_centers(conn: sqlite3.Connection, settings: Settings, profile_id: int, service: str) -> int:
    """Actualiza la lista del servicio con la sesión del perfil. Devuelve cuántos centros hay.

    Los favoritos del portal se añaden a los del perfil; los que se quiten aquí no se tocan en el portal.
    """
    found = with_session(conn, settings, profile_id, lambda: portal.fetch_centers(settings, profile_id, service))
    count = centers_repo.replace_service_centers(conn, service, ((c.portal_id, c.name, c.address) for c in found))
    centers_repo.add_favorites(conn, profile_id, (c.portal_id for c in found if c.favorite))
    conn.commit()
    return count
