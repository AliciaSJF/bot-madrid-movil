import sqlite3

import pytest
from cryptography.fernet import Fernet

from src import portal
from src.config import Settings
from src.core.connection import check_connection
from src.db import crypto
from src.db import profiles as profiles_repo
from src.db.database import connect, db_path, init_db


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, fernet_key=Fernet.generate_key().decode())


@pytest.fixture
def conn(settings):
    init_db(settings.data_dir)
    c = connect(settings.data_dir)
    yield c
    c.close()


@pytest.fixture
def profile_id(conn, settings):
    pid = profiles_repo.create_profile(conn, "Alicia", "#0f766e", "a@example.com", crypto.encrypt(settings, "secreta"))
    conn.commit()
    return pid


def test_success_is_saved(monkeypatch, conn, settings, profile_id):
    seen = {}

    def fake_ensure(_settings, pid, username, password):
        seen.update(pid=pid, username=username, password=password)
        return portal.SessionResult(reused=False)

    monkeypatch.setattr(portal, "ensure_session", fake_ensure)
    check = check_connection(conn, settings, profile_id)

    assert check.ok
    assert seen == {"pid": profile_id, "username": "a@example.com", "password": "secreta"}
    profile = profiles_repo.get_profile(conn, profile_id)
    assert profile.portal_status == "ok"
    assert profile.portal_checked_at is not None


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (portal.LoginRejected("no"), "rechazado"),
        (portal.VerificationRequired("código"), "verificacion"),
        (portal.CaptchaDetected("captcha"), "verificacion"),
        (portal.PortalError("red"), "error"),
    ],
)
def test_failures_are_saved(monkeypatch, conn, settings, profile_id, error, status):
    def fake_ensure(*_args):
        raise error

    monkeypatch.setattr(portal, "ensure_session", fake_ensure)
    check = check_connection(conn, settings, profile_id)
    assert check.status == status
    assert profiles_repo.get_profile(conn, profile_id).portal_status == status


def test_migration_adds_portal_columns_to_old_db(tmp_path):
    old = sqlite3.connect(db_path(tmp_path))
    old.execute(
        "CREATE TABLE profiles (id INTEGER PRIMARY KEY, display_name TEXT, color TEXT, "
        "portal_username TEXT, portal_password_enc TEXT, telegram_chat_id TEXT, created_at TEXT)"
    )
    old.execute("INSERT INTO profiles VALUES (1, 'A', '#000', 'u', 'p', NULL, '2026-01-01')")
    old.commit()
    old.close()

    init_db(tmp_path)
    conn = connect(tmp_path)
    assert profiles_repo.get_profile(conn, 1).portal_status == "sin_probar"
    conn.close()
