"""Anular desde la app, con el portal simulado (nunca el real)."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright

from src import portal
from src.api.app import create_app
from src.config import Settings
from src.core import cancel as core_cancel
from src.db import centers as centers_repo
from src.db import crypto
from src.db import jobs as jobs_repo
from src.db import profiles as profiles_repo
from src.db.database import connect, init_db
from src.portal.cancel import CancelResult, _find_row

MADRID = ZoneInfo("Europe/Madrid")
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def settings(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path, fernet_key=Fernet.generate_key().decode())
    init_db(s.data_dir)
    conn = connect(s.data_dir)
    for name in ("Alicia", "Novio"):
        profiles_repo.create_profile(conn, name, "#0f766e", f"{name}@example.com", crypto.encrypt(s, "x"))
    centers_repo.replace_service_centers(conn, "multitrabajo", [(86, "Juan de Dios Román", "")])
    conn.commit()
    conn.close()
    return s


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(core_cancel, "notify", lambda conn, s, pid, text, buttons=None: messages.append((pid, text)) or True)
    return messages


def booked_job(settings, profile_id=1, status="reservado", days=3):
    conn = connect(settings.data_dir)
    slot = (datetime.now(MADRID) + timedelta(days=days)).replace(hour=19, minute=0, second=0, microsecond=0)
    job_id = jobs_repo.create_job(conn, profile_id, 86, "Juan de Dios Román", "SALA MUSCULACION", "multitrabajo",
                                  slot, "reservar", None, False)
    jobs_repo.set_status(conn, job_id, status)
    conn.commit()
    conn.close()
    return job_id, slot


def test_cancel_marks_job_and_confirms_by_telegram(settings, sent, monkeypatch):
    job_id, slot = booked_job(settings)
    calls = []

    def fake_cancel(_settings, profile_id, day, start, center):
        calls.append((profile_id, day, start, center))
        return CancelResult("anulado", "Anulada en el portal.", Decimal("24.00"))

    monkeypatch.setattr(portal, "cancel_reservation", fake_cancel)
    conn = connect(settings.data_dir)
    outcome = core_cancel.cancel_booking(conn, settings, job_id, 1)

    assert outcome.ok
    assert calls == [(1, slot.date(), time(19, 0), "Juan de Dios Román")]
    assert jobs_repo.get_job(conn, job_id).status == "anulado"
    assert sent[-1][0] == 1
    assert sent[-1][1].startswith("🗑️ Reserva anulada") and "24,00 €" in sent[-1][1]
    conn.close()


def test_cannot_cancel_someone_elses_booking(settings, sent, monkeypatch):
    job_id, _ = booked_job(settings, profile_id=2)
    monkeypatch.setattr(portal, "cancel_reservation", lambda *a: pytest.fail("no debe tocar el portal"))
    conn = connect(settings.data_dir)
    assert not core_cancel.cancel_booking(conn, settings, job_id, 1).ok
    assert jobs_repo.get_job(conn, job_id).status == "reservado"
    assert sent == []
    conn.close()


@pytest.mark.parametrize(("status", "days"), [("reservado", -1), ("pendiente", 3), ("anulado", 3), ("fallido", 3)])
def test_only_future_booked_jobs_can_be_cancelled(settings, sent, monkeypatch, status, days):
    job_id, _ = booked_job(settings, status=status, days=days)
    monkeypatch.setattr(portal, "cancel_reservation", lambda *a: pytest.fail("no debe tocar el portal"))
    conn = connect(settings.data_dir)
    assert not core_cancel.cancel_booking(conn, settings, job_id, 1).ok
    conn.close()


def test_portal_refusal_is_reported(settings, sent, monkeypatch):
    job_id, _ = booked_job(settings)
    monkeypatch.setattr(portal, "cancel_reservation",
                        lambda *a: CancelResult("no_permitido", "El portal no permite anular esta reserva (¿fuera de plazo?)."))
    conn = connect(settings.data_dir)
    outcome = core_cancel.cancel_booking(conn, settings, job_id, 1)
    assert not outcome.ok and "fuera de plazo" in outcome.message
    assert jobs_repo.get_job(conn, job_id).status == "reservado"
    assert sent[-1][1].startswith("❌ No se pudo anular")
    conn.close()


def test_web_shows_annul_button_only_for_booked(settings, monkeypatch):
    booked, _ = booked_job(settings)
    booked_job(settings, status="fallido")
    client = TestClient(create_app(settings), follow_redirects=False)
    client.post("/configurar", data={"password": "casa-segura-123", "password2": "casa-segura-123"})
    client.post("/perfiles/1/elegir")

    page = client.get("/reservas").text
    assert page.count("Anular reserva") == 1
    assert f"/reservas/{booked}/anular" in page

    monkeypatch.setattr("src.api.routers.jobs.core_cancel.cancel_booking",
                        lambda conn, s, job_id, pid: core_cancel.CancelOutcome(True, "Reserva anulada."))
    assert client.post(f"/reservas/{booked}/anular").headers["location"] == "/reservas"
    assert "Reserva anulada." in client.get("/reservas").text


def test_find_row_uses_start_time_column_and_center():
    html = (FIXTURES / "reservations_page.html").read_text(encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.set_content(html)
        ids = lambda row: row.locator("td").nth(1).inner_text() if row else None  # noqa: E731
        # 15:30 es el fin de la fila 1 y el inicio de la 2: tiene que elegir la 2
        assert ids(_find_row(page, date(2026, 10, 2), time(15, 30), "Daoíz y Velarde")) == "2"
        # Dos filas a las 19:00 del sábado: decide el centro, aunque el portal lo escriba sin tilde
        assert ids(_find_row(page, date(2026, 10, 3), time(19, 0), "Juan de Dios Román")) == "3"
        assert ids(_find_row(page, date(2026, 10, 3), time(19, 0), "Daoíz y Velarde")) == "4"
        # Dos filas y ningún centro coincide: mejor no elegir ninguna
        assert _find_row(page, date(2026, 10, 3), time(19, 0), "Aluche") is None
        assert _find_row(page, date(2026, 10, 9), time(19, 0), "Aluche") is None
        browser.close()
