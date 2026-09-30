"""Probar la conexión de un perfil con el portal y guardar el resultado."""

import sqlite3
from dataclasses import dataclass

from src import portal
from src.config import Settings
from src.db import crypto
from src.db import profiles as profiles_repo


@dataclass(frozen=True)
class ConnectionCheck:
    status: str  # clave de profiles_repo.PORTAL_STATUS_LABELS
    message: str

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def check_connection(conn: sqlite3.Connection, settings: Settings, profile_id: int) -> ConnectionCheck:
    """Un único intento: reutiliza la sesión guardada o inicia sesión. Guarda el resultado en el perfil."""
    profile = profiles_repo.get_profile(conn, profile_id)
    if profile is None:
        raise LookupError(f"No existe el perfil {profile_id}")

    password = crypto.decrypt(settings, profiles_repo.get_portal_password_enc(conn, profile_id))
    try:
        result = portal.ensure_session(settings, profile_id, profile.portal_username, password)
        check = ConnectionCheck("ok", result.message)
    except portal.LoginRejected as exc:
        check = ConnectionCheck("rechazado", str(exc))
    except (portal.VerificationRequired, portal.CaptchaDetected) as exc:
        check = ConnectionCheck("verificacion", str(exc))
    except portal.PortalError as exc:
        check = ConnectionCheck("error", str(exc))
    finally:
        del password

    profiles_repo.set_portal_status(conn, profile_id, check.status, check.message)
    conn.commit()
    return check
