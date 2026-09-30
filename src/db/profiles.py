"""Perfiles: una persona = una cuenta del portal."""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

# Colores de las tarjetas de perfil (contraste suficiente con texto blanco)
COLORS = ["#c2410c", "#0f766e", "#1d4ed8", "#7e22ce", "#be123c", "#4d7c0f"]

_COLUMNS = "id, display_name, color, portal_username"


@dataclass(frozen=True)
class Profile:
    id: int
    display_name: str
    color: str
    portal_username: str

    @property
    def initial(self) -> str:
        return self.display_name[:1].upper()


def _from_row(row: sqlite3.Row) -> Profile:
    return Profile(row["id"], row["display_name"], row["color"], row["portal_username"])


def list_profiles(conn: sqlite3.Connection) -> list[Profile]:
    rows = conn.execute(f"SELECT {_COLUMNS} FROM profiles ORDER BY id").fetchall()
    return [_from_row(r) for r in rows]


def get_profile(conn: sqlite3.Connection, profile_id: int) -> Profile | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM profiles WHERE id = ?", (profile_id,)).fetchone()
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
