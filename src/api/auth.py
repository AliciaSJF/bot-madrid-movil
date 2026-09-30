"""Cuenta de casa: contraseña (hash scrypt de la stdlib) y estado de la sesión del navegador."""

import hashlib
import hmac
import secrets
import sqlite3

from src.db.database import get_setting, set_setting

HOUSE_PASSWORD_KEY = "house_password_hash"
MIN_HOUSE_PASSWORD = 8

_N, _R, _P = 2**14, 8, 1


# --- hash ------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=_N, r=_R, p=_P)
    return hmac.compare_digest(digest, bytes.fromhex(digest_hex))


# --- contraseña de casa en la BD ---------------------------------------------

def house_password_is_set(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, HOUSE_PASSWORD_KEY) is not None


def set_house_password(conn: sqlite3.Connection, password: str) -> None:
    set_setting(conn, HOUSE_PASSWORD_KEY, hash_password(password))


def check_house_password(conn: sqlite3.Connection, password: str) -> bool:
    stored = get_setting(conn, HOUSE_PASSWORD_KEY)
    return stored is not None and verify_password(password, stored)


def validate_new_house_password(password: str, repeated: str) -> str | None:
    """Devuelve el mensaje de error o None si vale."""
    if len(password) < MIN_HOUSE_PASSWORD:
        return f"Mínimo {MIN_HOUSE_PASSWORD} caracteres."
    if password != repeated:
        return "Las contraseñas no coinciden."
    return None


# --- sesión del navegador ----------------------------------------------------

def start_house_session(session: dict) -> None:
    session.clear()
    session["house"] = True


def has_house_session(session: dict) -> bool:
    return bool(session.get("house"))


def current_profile_id(session: dict) -> int | None:
    return session.get("profile_id")


def set_current_profile(session: dict, profile_id: int) -> None:
    session["profile_id"] = profile_id
