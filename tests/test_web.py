import sqlite3
from datetime import datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.config import Settings
from src.db import centers as centers_repo
from src.db import crypto
from src.db import slots as slots_repo
from src.db.database import connect, db_path

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


CHOPERA = 30


def seed_centers(settings):
    """Lista de centros como si se hubiera actualizado desde el portal."""
    conn = connect(settings.data_dir)
    centers_repo.replace_service_centers(
        conn, "multitrabajo", [(CHOPERA, "La Chopera", "Paseo de Fernán Núñez, 3"), (1, "Aluche", "Av. de las Águilas, 14")]
    )
    centers_repo.replace_service_centers(conn, "piscina", [(1, "Aluche", "Av. de las Águilas, 14")])
    conn.commit()
    conn.close()


def seed_slot(settings, day, at="19:00", free=3, total=10, service="multitrabajo", center_id=CHOPERA):
    """Turno guardado como si se hubiera consultado en el portal."""
    from datetime import time

    conn = connect(settings.data_dir)
    slots_repo.replace_days(
        conn, service, center_id, [day],
        [slots_repo.StoredSlot(day, time.fromisoformat(at), "SALA", free, total, free > 0)],
        datetime.now(settings.zone),
    )
    conn.commit()
    conn.close()


def book(client, settings, days=2, center_id=CHOPERA, service="multitrabajo", **overrides):
    seed_centers(settings)
    day = (datetime.now(settings.zone) + timedelta(days=days)).date()
    seed_slot(settings, day, service=service, center_id=center_id)
    data = {
        "slot_date": day.isoformat(),
        "slot_time": "19:00",
        "mode": "reservar",
        "on_free": "reservar",
        "activity": "SALA",
    } | overrides
    return client.post(f"/reservar/{service}/{center_id}/programar", data=data)


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
    assert r.headers["location"] == "/perfil"

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
    assert "Prueba" not in page  # el modo prueba ya no existe


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
    assert "Planificada" in client.get("/reservas").text

    client.post("/reservas/1/cancelar")
    assert "Cancelado" in client.get("/reservas").text


def test_all_screens_render(client, settings):
    setup_house(client)
    assert client.get("/perfiles/nuevo").status_code == 200
    create_profile(client)
    book(client, settings)
    day = (datetime.now(settings.zone) + timedelta(days=2)).date().isoformat()
    for url in (
        "/perfiles", "/perfil", "/reservar", "/reservar/multitrabajo", f"/reservar/multitrabajo/{CHOPERA}",
        f"/reservar/multitrabajo/{CHOPERA}/programar?dia={day}&hora=19:00&actividad=SALA", "/reservas",
    ):
        assert client.get(url).status_code == 200, url


def test_account_page_and_connection_check(client, monkeypatch):
    from src.api.routers import account
    from src.core.connection import ConnectionCheck

    setup_house(client)
    create_profile(client)
    assert "Sin probar" in client.get("/perfil").text

    def fake_check(conn, _settings, profile_id):
        from src.db import profiles as profiles_repo

        profiles_repo.set_portal_status(conn, profile_id, "ok", "Sesión iniciada correctamente.")
        conn.commit()
        return ConnectionCheck("ok", "Sesión iniciada correctamente.")

    monkeypatch.setattr(account, "check_connection", fake_check)
    assert client.post("/perfil/probar").headers["location"] == "/perfil"
    page = client.get("/perfil").text
    assert "Conectada" in page and "Conexión correcta" in page


def test_center_list_is_per_service_with_favorites_first(client, settings):
    setup_house(client)
    create_profile(client)
    seed_centers(settings)

    page = client.get("/reservar/multitrabajo").text
    assert page.index("Aluche") < page.index("La Chopera")  # por nombre
    assert "La Chopera" not in client.get("/reservar/piscina").text  # no tiene nado libre

    r = client.post(f"/favoritos/{CHOPERA}", data={"favorite": "true"}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 204
    page = client.get("/reservar/multitrabajo").text
    assert page.index("La Chopera") < page.index("Aluche")  # favoritos primero


def test_center_not_offering_service_goes_back_to_list(client, settings):
    setup_house(client)
    create_profile(client)
    r = book(client, settings, center_id=CHOPERA, service="piscina")
    assert r.headers["location"] == "/reservar/piscina"


def test_unknown_service_is_rejected(client):
    setup_house(client)
    create_profile(client)
    assert client.get("/reservar/padel").status_code == 422


def test_refresh_centers_shows_portal_error(client, monkeypatch):
    from src import portal
    from src.api.routers import booking

    setup_house(client)
    create_profile(client)

    def failing(*_args):
        raise portal.PortalError("Problema de red al abrir el portal (ERR_X).")

    monkeypatch.setattr(booking, "refresh_centers", failing)
    r = client.post("/reservar/multitrabajo/actualizar")
    assert r.headers["location"] == "/reservar/multitrabajo"
    assert "No se pudo actualizar" in client.get("/reservar/multitrabajo").text


def test_slots_page_shows_status_and_links(client, settings):
    setup_house(client)
    create_profile(client)
    seed_centers(settings)
    from datetime import time

    day = (datetime.now(settings.zone) + timedelta(days=1)).date()
    conn = connect(settings.data_dir)
    slots_repo.replace_days(conn, "multitrabajo", CHOPERA, [day], [
        slots_repo.StoredSlot(day, time(19), "SALA", 0, 10, False),
        slots_repo.StoredSlot(day, time(20), "SALA", 4, 10, True),
    ], datetime.now(settings.zone))
    conn.commit(); conn.close()

    page = client.get(f"/reservar/multitrabajo/{CHOPERA}?dia={day.isoformat()}").text
    assert "Completo" in page and "4</strong>/10 libres" in page
    assert f"programar?dia={day.isoformat()}&amp;hora=20:00&amp;actividad=SALA" in page


def test_full_slot_offers_observe_first(client, settings):
    from datetime import time

    setup_house(client)
    create_profile(client)
    seed_centers(settings)
    day = (datetime.now(settings.zone) + timedelta(days=1)).date()
    conn = connect(settings.data_dir)
    slots_repo.replace_days(conn, "multitrabajo", CHOPERA, [day], [slots_repo.StoredSlot(day, time(19), "SALA", 0, 10, False)],
                            datetime.now(settings.zone))
    conn.commit(); conn.close()

    page = client.get(f"/reservar/multitrabajo/{CHOPERA}/programar?dia={day.isoformat()}&hora=19:00&actividad=SALA").text
    assert page.index('value="observar"') < page.index('value="reservar"')
    assert 'value="observar" checked' in page


def test_unknown_slot_goes_back_to_week(client, settings):
    setup_house(client)
    create_profile(client)
    seed_centers(settings)
    r = client.get(f"/reservar/multitrabajo/{CHOPERA}/programar?dia=2030-01-01&hora=19:00")
    assert r.headers["location"].startswith(f"/reservar/multitrabajo/{CHOPERA}")


def test_pwa_files_are_served(client):
    assert client.get("/manifest.webmanifest").headers["content-type"].startswith("application/manifest+json")
    assert client.get("/sw.js").status_code == 200
    assert client.get("/static/icon-192.png").status_code == 200


def test_messages_say_what_will_happen(client, settings, monkeypatch):
    """Planificar (aún no abre) no es lo mismo que reservar ya (está abierto) ni que observar."""
    started = []

    class FakeWorker:
        def start_booking(self, job_id):
            started.append(job_id)

    monkeypatch.setattr("src.api.routers.booking.current_worker", lambda: FakeWorker())
    setup_house(client)
    create_profile(client)

    book(client, settings, days=4)  # abre dentro de unos días
    page = client.get("/reservas").text
    assert "Reserva planificada para el" in page and "cuando abra el turno" in page
    assert "Planificada" in page and "Se reservará en cuanto abra" in page
    assert started == []  # la lanza el programador un minuto antes de abrir

    book(client, settings, days=1)  # ya está abierto: se reserva ahora mismo
    assert "Reservando ahora" in client.get("/reservas").text
    assert started == [2]

    book(client, settings, days=1, mode="observar")
    assert "Vigilando el turno" in client.get("/reservas").text
    assert started == [2]


def test_profile_asks_for_the_madrid_movil_email(client):
    setup_house(client)
    page = client.get("/perfiles/nuevo").text
    assert "Email del portal" in page and "app Madrid Móvil" in page and 'class="info-btn"' in page
    r = client.post("/perfiles/nuevo", data={"display_name": "Alicia", "color": "#0f766e",
                                             "portal_username": "alicia", "portal_password": "x"})
    assert r.status_code == 400 and "Pon el email con el que entras en Madrid Móvil" in r.text


def test_center_list_explains_the_first_sync(client, settings):
    setup_house(client)
    create_profile(client)
    page = client.get("/reservar/multitrabajo").text
    assert "Sincronizar con el portal" in page and "solo hace falta una vez" in page
    seed_centers(settings)
    page = client.get("/reservar/multitrabajo").text
    assert "Sincronizar con el portal" not in page
    assert 'class="icon-btn refresh-btn"' in page and page.index("refresh-btn") < page.index("center-list")
