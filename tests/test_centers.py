from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright

from src import portal
from src.config import Settings
from src.core import centers as core_centers
from src.core import connection as core_connection
from src.core.connection import ConnectionCheck
from src.db import centers as centers_repo
from src.db import crypto
from src.db import profiles as profiles_repo
from src.db.database import connect, init_db
from src.portal.centers import PortalCenter, read_centers

FIXTURES = Path(__file__).parent / "fixtures"


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
    pid = profiles_repo.create_profile(conn, "Alicia", "#0f766e", "a@example.com", crypto.encrypt(settings, "x"))
    conn.commit()
    return pid


def test_read_centers_from_portal_markup():
    html = (FIXTURES / "centers_page.html").read_text(encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.set_content(html)
        centers = read_centers(page)
        browser.close()
    assert centers == [
        PortalCenter(1, "Aluche", "Avenida de las Águilas, 14 (Latina), 28044, Madrid", False),
        PortalCenter(58, "Daoíz y Velarde", "Plaza de Daoíz y Velarde, 5 (Retiro), 28007, Madrid", True),
    ]


def test_replace_service_centers_keeps_other_services(conn, profile_id):
    centers_repo.replace_service_centers(conn, "multitrabajo", [(1, "Aluche", "a"), (30, "La Chopera", "b")])
    centers_repo.replace_service_centers(conn, "piscina", [(1, "Aluche", "a")])
    centers_repo.replace_service_centers(conn, "multitrabajo", [(30, "La Chopera", "b")])  # Aluche deja la sala

    assert [c.name for c in centers_repo.list_centers(conn, "multitrabajo", profile_id)] == ["La Chopera"]
    assert [c.name for c in centers_repo.list_centers(conn, "piscina", profile_id)] == ["Aluche"]
    assert centers_repo.get_center(conn, 1, "multitrabajo") is None
    assert centers_repo.get_center(conn, 1, "piscina").name == "Aluche"


def test_favorites_are_per_profile(conn, settings, profile_id):
    other = profiles_repo.create_profile(conn, "Pareja", "#1d4ed8", "b@example.com", crypto.encrypt(settings, "y"))
    centers_repo.replace_service_centers(conn, "multitrabajo", [(1, "Aluche", ""), (30, "La Chopera", "")])
    centers_repo.set_favorite(conn, profile_id, 30, True)

    assert [c.name for c in centers_repo.list_centers(conn, "multitrabajo", profile_id)] == ["La Chopera", "Aluche"]
    assert [c.name for c in centers_repo.list_centers(conn, "multitrabajo", other)] == ["Aluche", "La Chopera"]

    centers_repo.set_favorite(conn, profile_id, 30, False)
    assert not any(c.is_favorite for c in centers_repo.list_centers(conn, "multitrabajo", profile_id))


def test_refresh_saves_list_and_portal_favorites(monkeypatch, conn, settings, profile_id):
    found = [PortalCenter(1, "Aluche", "a", False), PortalCenter(58, "Daoíz y Velarde", "b", True)]
    monkeypatch.setattr(portal, "fetch_centers", lambda *_: found)

    assert core_centers.refresh_centers(conn, settings, profile_id, "multitrabajo") == 2
    listed = centers_repo.list_centers(conn, "multitrabajo", profile_id)
    assert [(c.name, c.is_favorite) for c in listed] == [("Daoíz y Velarde", True), ("Aluche", False)]


def test_refresh_logs_in_again_once_if_session_expired(monkeypatch, conn, settings, profile_id):
    calls = {"fetch": 0, "login": 0}

    def fetch(*_args):
        calls["fetch"] += 1
        if calls["fetch"] == 1:
            raise portal.SessionExpired("caducada")
        return [PortalCenter(1, "Aluche", "a", False)]

    def login(*_args):
        calls["login"] += 1
        return ConnectionCheck("ok", "Sesión iniciada correctamente.")

    monkeypatch.setattr(portal, "fetch_centers", fetch)
    monkeypatch.setattr(core_connection, "check_connection", login)

    assert core_centers.refresh_centers(conn, settings, profile_id, "multitrabajo") == 1
    assert calls == {"fetch": 2, "login": 1}


def test_refresh_stops_if_login_fails(monkeypatch, conn, settings, profile_id):
    def fetch(*_args):
        raise portal.SessionExpired("caducada")

    monkeypatch.setattr(portal, "fetch_centers", fetch)
    monkeypatch.setattr(core_connection, "check_connection", lambda *_: ConnectionCheck("rechazado", "Contraseña mal"))

    with pytest.raises(portal.PortalError, match="Contraseña mal"):
        core_centers.refresh_centers(conn, settings, profile_id, "multitrabajo")
