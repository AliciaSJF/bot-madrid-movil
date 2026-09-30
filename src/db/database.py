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
    finally:
        conn.close()


def get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
