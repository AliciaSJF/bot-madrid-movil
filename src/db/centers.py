"""Polideportivos guardados localmente y favoritos por perfil.

La lista se copia del portal al pulsar "Actualizar" para no consultarlo cada vez que se abre la pantalla.
"""

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Center:
    portal_id: int
    name: str
    address: str
    is_favorite: bool = False


def replace_service_centers(
    conn: sqlite3.Connection, service: str, centers: Iterable[tuple[int, str, str]]
) -> int:
    """Guarda (portal_id, nombre, dirección) de un servicio y sustituye la lista anterior de ese servicio."""
    now = datetime.now(UTC).isoformat()
    rows = list(centers)
    conn.executemany(
        "INSERT INTO centers (portal_id, name, address, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(portal_id) DO UPDATE SET name = excluded.name, address = excluded.address, "
        "updated_at = excluded.updated_at",
        [(pid, name, address, now) for pid, name, address in rows],
    )
    conn.execute("DELETE FROM center_services WHERE service = ?", (service,))
    conn.executemany(
        "INSERT INTO center_services (portal_id, service) VALUES (?, ?)",
        [(pid, service) for pid, _, _ in rows],
    )
    return len(rows)


def list_centers(conn: sqlite3.Connection, service: str, profile_id: int) -> list[Center]:
    """Centros del servicio: favoritos del perfil primero y después por nombre."""
    rows = conn.execute(
        "SELECT c.portal_id, c.name, c.address, f.portal_id IS NOT NULL AS is_favorite "
        "FROM centers c "
        "JOIN center_services s ON s.portal_id = c.portal_id AND s.service = ? "
        "LEFT JOIN favorites f ON f.portal_id = c.portal_id AND f.profile_id = ? "
        "ORDER BY is_favorite DESC, c.name COLLATE NOCASE",
        (service, profile_id),
    ).fetchall()
    return [Center(r["portal_id"], r["name"], r["address"], bool(r["is_favorite"])) for r in rows]


def get_center(conn: sqlite3.Connection, portal_id: int, service: str | None = None) -> Center | None:
    """Un centro; si se indica servicio, solo si lo ofrece."""
    query = "SELECT c.portal_id, c.name, c.address FROM centers c"
    params: tuple = (portal_id,)
    if service:
        query += " JOIN center_services s ON s.portal_id = c.portal_id AND s.service = ?"
        params = (service, portal_id)
    row = conn.execute(query + " WHERE c.portal_id = ?", params).fetchone()
    return Center(row["portal_id"], row["name"], row["address"]) if row else None


def last_updated(conn: sqlite3.Connection, service: str) -> datetime | None:
    row = conn.execute(
        "SELECT MAX(c.updated_at) AS t FROM centers c "
        "JOIN center_services s ON s.portal_id = c.portal_id AND s.service = ?",
        (service,),
    ).fetchone()
    return datetime.fromisoformat(row["t"]) if row and row["t"] else None


def add_favorites(conn: sqlite3.Connection, profile_id: int, portal_ids: Iterable[int]) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO favorites (profile_id, portal_id) VALUES (?, ?)",
        [(profile_id, pid) for pid in portal_ids],
    )


def set_favorite(conn: sqlite3.Connection, profile_id: int, portal_id: int, favorite: bool) -> None:
    if favorite:
        add_favorites(conn, profile_id, [portal_id])
    else:
        conn.execute("DELETE FROM favorites WHERE profile_id = ? AND portal_id = ?", (profile_id, portal_id))
