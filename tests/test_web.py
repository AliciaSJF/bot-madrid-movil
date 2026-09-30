import sqlite3
from datetime import datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.config import Settings
from src.db import crypto
from src.db.database import db_path

HOUSE_PASSWORD = "casa-segura-123"
PORTAL_PASSWORD = "portal-secreto-456"


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, fernet_key=Fernet.generate_key().decode())


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings), follow_redirects=False)


def setup_house(client):
    r = client.post("/configurar", data={"password": HOUSE_PASSWORD, "password2": HOUSE_PASSWORD})
    assert r.status_code == 303


def create_profile(client, name="Alicia"):
    r = client.post(
        "/perfiles/nuevo",
        data={
            "display_name": name,
            "color": "#0f766e",
            "portal_username": f"{name.lower()}@example.com",
            "portal_password": PORTAL_PASSWORD,
        },
    )
    assert r.status_code == 303, r.text
    return r


def book(client, settings, days=2, **overrides):
    slot = datetime.now(settings.zone) + timedelta(days=days)
    data = {
        "center": "La Chopera",
        "service": "multitrabajo",
        "slot_date": slot.date().isoformat(),
        "slot_time": "19:00",
        "mode": "reservar",
        "on_free": "reservar",
    } | overrides
    return client.post("/reservar", data=data)


def test_first_visit_asks_to_create_house_password(client):
    r = client.get("/")
    assert r.headers["location"] == "/configurar"


def test_setup_rejects_mismatched_passwords(client):
    r = client.post("/configurar", data={"password": HOUSE_PASSWORD, "password2": "otra-cosa-123"})
    assert r.status_code == 400
    assert client.get("/").headers["location"] == "/configurar"


def test_setup_only_once(client):
    setup_house(client)
    r = client.post("/configurar", data={"password": "intruso-1234", "password2": "intruso-1234"})
    assert r.headers["location"] == "/entrar"


def test_login_with_house_password(client, settings):
    setup_house(client)
    other = TestClient(create_app(settings), follow_redirects=False)
    assert other.get("/perfiles").headers["location"] == "/entrar"
    assert other.post("/entrar", data={"password": "incorrecta"}).status_code == 401
    assert other.post("/entrar", data={"password": HOUSE_PASSWORD}).headers["location"] == "/perfiles"
    assert other.get("/perfiles").status_code == 200


def test_new_profile_stores_password_encrypted(client, settings):
    setup_house(client)
    r = create_profile(client)
    assert r.headers["location"] == "/reservas"

    raw = db_path(settings.data_dir).read_bytes()
    assert PORTAL_PASSWORD.encode() not in raw
    conn = sqlite3.connect(db_path(settings.data_dir))
    (enc,) = conn.execute("SELECT portal_password_enc FROM profiles").fetchone()
    conn.close()
    assert crypto.decrypt(settings, enc) == PORTAL_PASSWORD


def test_duplicate_profile_name_is_rejected(client):
    setup_house(client)
    create_profile(client, "Alicia")
    r = client.post(
        "/perfiles/nuevo",
        data={"display_name": "alicia", "color": "#0f766e", "portal_username": "x", "portal_password": "y"},
    )
    assert r.status_code == 400


def test_profile_picker_switches_profile(client):
    setup_house(client)
    create_profile(client, "Alicia")
    create_profile(client, "Pareja")
    page = client.get("/perfiles").text
    assert "Alicia" in page and "Pareja" in page
    assert client.post("/perfiles/1/elegir").headers["location"] == "/reservas"
    assert 'aria-label="Cambiar de perfil (Alicia)"' in client.get("/reservas").text


def test_booking_needs_a_profile(client):
    setup_house(client)
    assert client.get("/reservar").headers["location"] == "/perfiles"


def test_book_and_list_jobs(client, settings):
    setup_house(client)
    create_profile(client)
    r = book(client, settings, mode="observar", on_free="avisar")
    assert r.headers["location"] == "/reservas"
    page = client.get("/reservas").text
    assert "La Chopera" in page
    assert "Observar plazas libres" in page
    assert "Prueba" in page  # dry_run por defecto en el servidor


def test_booking_in_the_past_is_rejected(client, settings):
    setup_house(client)
    create_profile(client)
    r = book(client, settings, days=-1)
    assert r.status_code == 400
    assert "ya ha pasado" in r.text


def test_slot_is_stored_in_utc(client, settings):
    setup_house(client)
    create_profile(client)
    book(client, settings)
    conn = sqlite3.connect(db_path(settings.data_dir))
    (slot_at,) = conn.execute("SELECT slot_at FROM jobs").fetchone()
    conn.close()
    stored = datetime.fromisoformat(slot_at)
    assert stored.utcoffset() == timedelta(0)
    assert stored.astimezone(settings.zone).strftime("%H:%M") == "19:00"


def test_cancel_only_own_active_jobs(client, settings):
    setup_house(client)
    create_profile(client, "Alicia")
    book(client, settings)
    create_profile(client, "Pareja")  # queda elegido Pareja

    client.post("/reservas/1/cancelar")
    client.post("/perfiles/1/elegir")
    assert "Pendiente" in client.get("/reservas").text

    client.post("/reservas/1/cancelar")
    assert "Cancelado" in client.get("/reservas").text


def test_pwa_files_are_served(client):
    assert client.get("/manifest.webmanifest").headers["content-type"].startswith("application/manifest+json")
    assert client.get("/sw.js").status_code == 200
    assert client.get("/static/icon-192.png").status_code == 200
