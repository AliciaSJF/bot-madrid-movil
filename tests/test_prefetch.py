from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet

from src import portal
from src.config import Settings
from src.core import prefetch
from src.db import centers as centers_repo
from src.db import crypto
from src.db import profiles as profiles_repo
from src.db import slots as slots_repo
from src.db.database import connect, init_db
from src.portal.centers import PortalCenter
from src.portal.slots import PortalSlot

MADRID = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=MADRID)


@pytest.fixture
def settings(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path, fernet_key=Fernet.generate_key().decode())
    init_db(s.data_dir)
    conn = connect(s.data_dir)
    profiles_repo.create_profile(conn, "Alicia", "#0f766e", "a@example.com", crypto.encrypt(s, "x"))
    conn.commit()
    conn.close()
    return s


@pytest.fixture
def fake_portal(monkeypatch):
    calls = {"centers": [], "slots": []}

    def fetch_centers(_settings, _pid, service):
        calls["centers"].append(service)
        return [PortalCenter(58, "Daoíz y Velarde", "", True), PortalCenter(1, "Aluche", "", False)]

    def fetch_slots_many(_settings, _pid, targets, days):
        calls["slots"].append(list(targets))
        return {t: [PortalSlot(days[0], time(19), "SALA", 3, 10, True)] for t in targets}

    monkeypatch.setattr(portal, "fetch_centers", fetch_centers)
    monkeypatch.setattr(portal, "fetch_slots_many", fetch_slots_many)
    return calls


def test_first_prefetch_loads_centers_and_favorite_slots_in_one_visit(settings, fake_portal):
    prefetch.prefetch(settings, 1, NOW)

    assert fake_portal["centers"] == ["multitrabajo", "piscina"]
    # Solo el favorito (58), de los dos servicios, en una única llamada
    assert fake_portal["slots"] == [[("multitrabajo", 58), ("piscina", 58)]]
    conn = connect(settings.data_dir)
    assert len(slots_repo.list_slots(conn, "piscina", 58, [NOW.date()])) == 1
    conn.close()


def test_second_prefetch_soon_after_does_not_touch_portal(settings, fake_portal):
    prefetch.prefetch(settings, 1, NOW)
    prefetch.prefetch(settings, 1, NOW + timedelta(minutes=2))
    assert len(fake_portal["centers"]) == 2
    assert len(fake_portal["slots"]) == 1


def test_prefetch_without_favorites_only_loads_centers(settings, fake_portal):
    prefetch.prefetch(settings, 1, NOW)
    conn = connect(settings.data_dir)
    centers_repo.set_favorite(conn, 1, 58, False)
    conn.commit()
    conn.close()

    prefetch.prefetch(settings, 1, NOW + timedelta(minutes=20))
    assert len(fake_portal["slots"]) == 1  # la primera; la segunda no tenía favoritos


def test_schedule_is_off_in_tests(settings):
    assert prefetch.schedule_prefetch(settings, 1) is False
