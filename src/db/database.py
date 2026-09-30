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
    telegram_link_code  TEXT,
    telegram_link_expires TEXT,
    portal_status       TEXT NOT NULL DEFAULT 'sin_probar',
    portal_checked_at   TEXT,
    portal_message      TEXT,
    created_at          TEXT NOT NULL
);

-- Polideportivos tal como los lista el portal; portal_id es su facility_code
CREATE TABLE IF NOT EXISTS centers (
    portal_id   INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    address     TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);

-- Qué servicios de uso libre ofrece cada centro (según la última actualización)
CREATE TABLE IF NOT EXISTS center_services (
    portal_id   INTEGER NOT NULL REFERENCES centers(portal_id) ON DELETE CASCADE,
    service     TEXT NOT NULL CHECK (service IN ('multitrabajo', 'piscina')),
    PRIMARY KEY (portal_id, service)
);

-- Favoritos de cada perfil (por centro, valen para todos los servicios)
CREATE TABLE IF NOT EXISTS favorites (
    profile_id  INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    portal_id   INTEGER NOT NULL REFERENCES centers(portal_id) ON DELETE CASCADE,
    PRIMARY KEY (profile_id, portal_id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY,
    profile_id  INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    center      TEXT NOT NULL,
    center_id   INTEGER REFERENCES centers(portal_id),
    activity    TEXT NOT NULL DEFAULT '',
    service     TEXT NOT NULL CHECK (service IN ('multitrabajo', 'piscina')),
    slot_at     TEXT NOT NULL,
    mode        TEXT NOT NULL CHECK (mode IN ('reservar', 'observar')),
    on_free     TEXT CHECK (on_free IN ('reservar', 'avisar')),
    dry_run     INTEGER NOT NULL DEFAULT 1,
    status      TEXT NOT NULL,
    result_message TEXT,
    source_job_id INTEGER REFERENCES jobs(id),
    created_at  TEXT NOT NULL,
    updated_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_profile ON jobs(profile_id, slot_at);

-- Historial de cada programación: qué hizo el bot y qué pasó (sin datos sensibles)
CREATE TABLE IF NOT EXISTS attempts (
    id          INTEGER PRIMARY KEY,
    job_id      INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    at          TEXT NOT NULL,
    action      TEXT NOT NULL,
    result      TEXT NOT NULL,
    message     TEXT NOT NULL DEFAULT ''
);

-- Última foto de los turnos de un centro/servicio/día (sirve a todos los perfiles).
-- day y time en hora de Madrid, tal como los muestra el portal.
CREATE TABLE IF NOT EXISTS slots (
    service     TEXT NOT NULL,
    center_id   INTEGER NOT NULL,
    day         TEXT NOT NULL,
    time        TEXT NOT NULL,
    activity    TEXT NOT NULL DEFAULT '',
    free        INTEGER NOT NULL,
    total       INTEGER NOT NULL,
    selectable  INTEGER NOT NULL,
    PRIMARY KEY (service, center_id, day, activity, time)
);

-- Cuándo se consultó cada día (también los días sin turnos)
CREATE TABLE IF NOT EXISTS slot_fetches (
    service     TEXT NOT NULL,
    center_id   INTEGER NOT NULL,
    day         TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (service, center_id, day)
);
"""


# Columnas añadidas después de crear la tabla: (tabla, columna, definición).
# CREATE TABLE IF NOT EXISTS no las añade en una BD que ya existía.
MIGRATIONS = [
    ("profiles", "portal_status", "TEXT NOT NULL DEFAULT 'sin_probar'"),
    ("profiles", "portal_checked_at", "TEXT"),
    ("profiles", "portal_message", "TEXT"),
    ("jobs", "center_id", "INTEGER REFERENCES centers(portal_id)"),
    ("jobs", "activity", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "telegram_link_code", "TEXT"),
    ("profiles", "telegram_link_expires", "TEXT"),
    ("jobs", "result_message", "TEXT"),
    ("jobs", "source_job_id", "INTEGER REFERENCES jobs(id)"),
    ("jobs", "updated_at", "TEXT"),
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
