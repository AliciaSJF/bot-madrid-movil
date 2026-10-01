"""«Mis reservas»: filtros, orden por cercanía y eliminar (sin tocar el portal)."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.config import Settings
from src.db import centers as centers_repo
from src.db import crypto
from src.db import jobs as jobs_repo
from src.db import profiles as profiles_repo
from src.db.database import connect, init_db

MADRID = ZoneInfo("Europe/Madrid")


@pytest.fixture
def settings(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path, fernet_key=Fernet.generate_key().decode())
    init_db(s.data_dir)
    conn = connect(s.data_dir)
    for name in ("Alicia", "Novio"):
        profiles_repo.create_profile(conn, name, "#0f766e", f"{name}@example.com", crypto.encrypt(s, "x"))
    centers_repo.replace_service_centers(conn, "multitrabajo", [(58, "Daoíz y Velarde", "")])
    conn.commit()
    conn.close()
    return s


def add(settings, hours, status="pendiente", dry_run=False, profile_id=1, mode="reservar"):
    conn = connect(settings.data_dir)
    slot = (datetime.now(MADRID) + timedelta(hours=hours)).replace(second=0, microsecond=0)
    job_id = jobs_repo.create_job(conn, profile_id, 58, "Daoíz y Velarde", "SALA MUSCULACION", "multitrabajo",
                                  slot, mode, None, dry_run)
    if status != "pendiente":
        jobs_repo.set_status(conn, job_id, status)
    conn.commit()
    conn.close()
    return job_id


def test_order_is_closest_first_on_both_sides(settings):
    far, near, yesterday, last_week = add(settings, 72), add(settings, 3), add(settings, -24), add(settings, -24 * 7)
    conn = connect(settings.data_dir)
    upcoming, past = jobs_repo.split_by_date(jobs_repo.list_jobs(conn, 1), datetime.now(MADRID))
    assert [j.id for j in upcoming] == [near, far]
    assert [j.id for j in past] == [yesterday, last_week]
    conn.close()


@pytest.mark.parametrize(
    ("status", "dry_run", "deletable"),
    [
        ("prueba_ok", True, True),
        ("fallido", False, True),
        ("cancelado", False, True),
        ("anulado", False, True),
        ("expirado", False, True),
        ("reservado", False, False),  # registro de un pago: nunca
        ("revisar", False, False),
        ("pendiente", True, False),  # en marcha: primero se cancela
        ("vigilando", False, False),
    ],
)
def test_what_can_be_deleted(settings, status, dry_run, deletable):
    job_id = add(settings, 5, status=status, dry_run=dry_run)
    conn = connect(settings.data_dir)
    assert jobs_repo.delete_job(conn, job_id, 1) is deletable
    assert (jobs_repo.get_job(conn, job_id) is None) is deletable
    conn.close()


def test_delete_keeps_watches_created_from_it_and_other_profiles(settings):
    failed = add(settings, 5, status="fallido")
    other = add(settings, 5, status="fallido", profile_id=2)
    conn = connect(settings.data_dir)
    watch = jobs_repo.create_watch_from(conn, jobs_repo.get_job(conn, failed))
    jobs_repo.add_attempt(conn, failed, "pulsar #1", "no", "completo")
    conn.commit()

    assert jobs_repo.delete_job(conn, other, 1) is False  # no es suyo
    assert jobs_repo.delete_job(conn, failed, 1) is True
    conn.commit()
    assert jobs_repo.get_job(conn, watch) is not None
    assert jobs_repo.list_attempts(conn, failed) == []
    conn.close()


def test_delete_all_tests_only_touches_finished_tests(settings):
    add(settings, 5, status="prueba_ok", dry_run=True)
    add(settings, -5, status="fallido", dry_run=True)
    running = add(settings, 5, status="esperando_apertura", dry_run=True)
    real = add(settings, 5, status="reservado")
    conn = connect(settings.data_dir)
    assert jobs_repo.delete_tests(conn, 1) == 2
    conn.commit()
    assert {j.id for j in jobs_repo.list_jobs(conn, 1)} == {running, real}
    conn.close()


@pytest.fixture
def client(settings):
    c = TestClient(create_app(settings), follow_redirects=False)
    c.post("/configurar", data={"password": "casa-segura-123", "password2": "casa-segura-123"})
    c.post("/perfiles/1/elegir")
    return c


def test_filters_and_counts(settings, client):
    add(settings, 5)  # programada
    add(settings, 5, status="reservado")
    add(settings, -5, status="fallido")
    add(settings, 5, status="prueba_ok", dry_run=True)

    page = client.get("/reservas").text  # por defecto, próximas
    assert 'aria-current="page">\n    Próximas <span class="filter-count">3</span>' in page
    assert "Pasadas" not in page

    assert client.get("/reservas?filtro=fallidas").text.count('class="card job') == 1
    assert client.get("/reservas?filtro=conseguidas").text.count('class="card job') == 1
    todas = client.get("/reservas?filtro=todas").text
    assert todas.count('class="card job') == 4
    assert todas.index("Próximas") < todas.index("Pasadas")
    assert client.get("/reservas?filtro=inventado").status_code == 200  # vuelve al de por defecto


def test_delete_buttons_and_bulk_delete(settings, client):
    test_id = add(settings, 5, status="prueba_ok", dry_run=True)
    real_id = add(settings, 5, status="reservado")

    page = client.get("/reservas?filtro=todas").text
    assert f"/reservas/{test_id}/eliminar" in page
    assert f"/reservas/{real_id}/eliminar" not in page

    assert "Eliminar todas las pruebas (1)" in client.get("/reservas?filtro=pruebas").text
    r = client.post("/reservas/eliminar-pruebas?filtro=pruebas")
    assert r.headers["location"] == "/reservas?filtro=pruebas"
    assert "1 prueba(s) eliminada(s)." in client.get("/reservas?filtro=pruebas").text

    # Una reserva conseguida no se deja borrar ni forzando la petición
    client.post(f"/reservas/{real_id}/eliminar")
    conn = connect(settings.data_dir)
    assert jobs_repo.get_job(conn, real_id) is not None
    conn.close()
