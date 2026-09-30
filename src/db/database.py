"""Conexión a SQLite y esquema. Los instantes se guardan en UTC (ISO 8601)."""

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    id                  INTEGER PRIMARY KEY,
    display_name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    color               TEXT NOT NULL,
    portal_username     TEXT NOT NULL,
    portal_password_enc TEXT NOT NULL,
    telegram_chat_id    TEXT,
    portal_status       TEXT NOT NULL DEFAULT 'sin_probar',
    portal_checked_at   TEXT,
    portal_message      TEXT,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    profile_id  INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    center      TEXT NOT NULL,
    service     TEXT NOT NULL CHECK (service IN ('multitrabajo', 'piscina')),
    slot_at     TEXT NOT NULL,
    mode        TEXT NOT NULL CHECK (mode IN ('reservar', 'observar')),
    on_free     TEXT CHECK (on_free IN ('reservar', 'avisar')),
    dry_run     INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_profile ON jobs(profile_id, slot_at);
"""


# Columnas añadidas después de crear la tabla: (tabla, columna, definición).
# CREATE TABLE IF NOT EXISTS no las añade en una BD que ya existía.
MIGRATIONS = [
    ("profiles", "portal_status", "TEXT NOT NULL DEFAULT 'sin_probar'"),
    ("profiles", "portal_checked_at", "TEXT"),
    ("profiles", "portal_message", "TEXT"),
]


def db_path(data_dir: Path) -> Path:
    return data_dir / "bot.db"


def connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path(data_dir))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(data_dir: Path) -> None:
    conn = connect(data_dir)
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, definition in MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
