"""Perfiles: una persona = una cuenta del portal."""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

# Colores de las tarjetas de perfil (contraste suficiente con texto blanco)
COLORS = ["#c2410c", "#0f766e", "#1d4ed8", "#7e22ce", "#be123c", "#4d7c0f"]

# Resultado de la última prueba de conexión al portal
PORTAL_STATUS_LABELS = {
    "sin_probar": "Sin probar",
    "ok": "Conectada",
    "rechazado": "Usuario o contraseña incorrectos",
    "verificacion": "El portal pide verificación",
    "error": "Error al conectar",
}

_COLUMNS = (
    "id, display_name, color, portal_username, portal_status, portal_checked_at, portal_message, telegram_chat_id"
)


@dataclass(frozen=True)
class Profile:
    id: int
    display_name: str
    color: str
    portal_username: str
    portal_status: str = "sin_probar"
    portal_checked_at: datetime | None = None
    portal_message: str | None = None
    telegram_chat_id: int | None = None

    @property
    def telegram_linked(self) -> bool:
        return self.telegram_chat_id is not None

    @property
    def initial(self) -> str:
        return self.display_name[:1].upper()


def _from_row(row: sqlite3.Row) -> Profile:
    checked = row["portal_checked_at"]
    return Profile(
        id=row["id"],
        display_name=row["display_name"],
        color=row["color"],
        portal_username=row["portal_username"],
        portal_status=row["portal_status"],
        portal_checked_at=datetime.fromisoformat(checked) if checked else None,
        portal_message=row["portal_message"],
        telegram_chat_id=int(row["telegram_chat_id"]) if row["telegram_chat_id"] else None,
    )


def list_profiles(conn: sqlite3.Connection) -> list[Profile]:
    rows = conn.execute(f"SELECT {_COLUMNS} FROM profiles ORDER BY id").fetchall()
    return [_from_row(r) for r in rows]


def get_profile(conn: sqlite3.Connection, profile_id: int) -> Profile | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    return _from_row(row) if row else None


def get_profile_by_name(conn: sqlite3.Connection, display_name: str) -> Profile | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM profiles WHERE display_name = ?", (display_name,)).fetchone()
    return _from_row(row) if row else None


def name_exists(conn: sqlite3.Connection, display_name: str) -> bool:
    row = conn.execute("SELECT 1 FROM profiles WHERE display_name = ?", (display_name,)).fetchone()
    return row is not None


def create_profile(
    conn: sqlite3.Connection,
    display_name: str,
    color: str,
    portal_username: str,
    portal_password_enc: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO profiles (display_name, color, portal_username, portal_password_enc, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (display_name, color, portal_username, portal_password_enc, datetime.now(UTC).isoformat()),
    )
    return cur.lastrowid


def get_portal_password_enc(conn: sqlite3.Connection, profile_id: int) -> str:
    """Contraseña del portal cifrada. Solo se descifra justo antes de usarla."""
    row = conn.execute("SELECT portal_password_enc FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    if row is None:
        raise LookupError(f"No existe el perfil {profile_id}")
    return row["portal_password_enc"]


def set_portal_status(conn: sqlite3.Connection, profile_id: int, status: str, message: str | None) -> None:
    if status not in PORTAL_STATUS_LABELS:
        raise ValueError(f"Estado de portal desconocido: {status}")
    conn.execute(
        "UPDATE profiles SET portal_status = ?, portal_checked_at = ?, portal_message = ? WHERE id = ?",
        (status, datetime.now(UTC).isoformat(), message, profile_id),
    )


# --- Telegram ------------------------------------------------------------------

def set_telegram_link_code(conn: sqlite3.Connection, profile_id: int, code: str, expires: datetime) -> None:
    conn.execute(
        "UPDATE profiles SET telegram_link_code = ?, telegram_link_expires = ? WHERE id = ?",
        (code, expires.astimezone(UTC).isoformat(), profile_id),
    )


def find_by_link_code(conn: sqlite3.Connection, code: str, now: datetime) -> int | None:
    """Perfil con ese código de vinculación, si no ha caducado."""
    row = conn.execute(
        "SELECT id, telegram_link_expires FROM profiles WHERE telegram_link_code = ?", (code,)
    ).fetchone()
    if row is None or datetime.fromisoformat(row["telegram_link_expires"]) < now:
        return None
    return row["id"]


def has_pending_link(conn: sqlite3.Connection, profile_id: int, now: datetime) -> bool:
    row = conn.execute("SELECT telegram_link_expires FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    return bool(row and row["telegram_link_expires"] and datetime.fromisoformat(row["telegram_link_expires"]) >= now)


def set_telegram_chat(conn: sqlite3.Connection, profile_id: int, chat_id: int | None) -> None:
    """Guarda (o quita, con None) el chat del perfil y anula cualquier código pendiente."""
    conn.execute(
        "UPDATE profiles SET telegram_chat_id = ?, telegram_link_code = NULL, telegram_link_expires = NULL "
        "WHERE id = ?",
        (str(chat_id) if chat_id is not None else None, profile_id),
    )
