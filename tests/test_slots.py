from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright

from src import portal
from src.config import Settings
from src.core import slots as core_slots
from src.db import slots as slots_repo
from src.db.database import connect, init_db
from src.portal.slots import PortalSlot, read_slots

MADRID = ZoneInfo("Europe/Madrid")
FIXTURES = Path(__file__).parent / "fixtures"


def stored(day, at, free, total=10, selectable=None):
    return slots_repo.StoredSlot(day, at, "SALA", free, total, free > 0 if selectable is None else selectable)


# --- lectura del HTML del portal ---------------------------------------------

def test_read_slots_from_portal_markup():
    html = (FIXTURES / "slots_page.html").read_text(encoding="utf-8")
    day = date(2026, 9, 30)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.set_content(html)
        slots = read_slots(page, day)
        browser.close()
    assert slots == [
        PortalSlot(day, time(9, 0), "Nado libre · Calle central", 0, 10, False),
        PortalSlot(day, time(10, 0), "Nado libre · Calle central", 4, 10, True),
        PortalSlot(day, time(10, 0), "Nado libre · Calle CON BORDILLO", 2, 6, True),
        PortalSlot(day, time(18, 0), "SALA MUSCULACION", 1, 20, True),  # la edad no es subtítulo
    ]


# --- estado de cada turno -----------------------------------------------------

def test_opening_is_49_hours_before():
    start = datetime(2026, 10, 2, 17, 0, tzinfo=MADRID)
    assert core_slots.opening_time(start) == datetime(2026, 9, 30, 16, 0, tzinfo=MADRID)


def test_opening_across_dst_change_uses_real_hours():
    # 26 oct 2026 10:00 (UTC+1) − 49 h reales = 24 oct 10:00 (UTC+2): 49 h de pared serían las 09:00
    start = datetime(2026, 10, 26, 10, 0, tzinfo=MADRID)
    opens = core_slots.opening_time(start)
    assert (start.astimezone(UTC) - opens.astimezone(UTC)) == timedelta(hours=49)
    assert opens.replace(tzinfo=None) == datetime(2026, 10, 24, 10, 0)


@pytest.mark.parametrize(
    ("slot", "status"),
    [
        (stored(date(2026, 9, 30), time(14, 30), 1), "pasado"),
        (stored(date(2026, 10, 1), time(14, 30), 9), "libre"),
        (stored(date(2026, 10, 1), time(18, 0), 0), "completo"),
        (stored(date(2026, 10, 1), time(18, 0), 3, selectable=False), "completo"),
        (stored(date(2026, 10, 3), time(10, 0), 20, 20), "sin_abrir"),
    ],
)
def test_classify(slot, status):
    now = datetime(2026, 9, 30, 15, 36, tzinfo=MADRID)
    assert core_slots.classify(slot, now, MADRID).status == status


def test_week_starts_today():
    assert core_slots.week(date(2026, 9, 30)) == [date(2026, 9, 30) + timedelta(days=i) for i in range(7)]


# --- guardar y refrescar ------------------------------------------------------

@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, data_dir=tmp_path, fernet_key=Fernet.generate_key().decode())


@pytest.fixture
def conn(settings):
    init_db(settings.data_dir)
    c = connect(settings.data_dir)
    yield c
    c.close()


def test_replace_days_overwrites_only_those_days(conn):
    d1, d2 = date(2026, 10, 1), date(2026, 10, 2)
    t0 = datetime(2026, 9, 30, 12, 0, tzinfo=MADRID)
    slots_repo.replace_days(conn, "piscina", 58, [d1, d2], [stored(d1, time(9), 1), stored(d2, time(9), 2)], t0)
    slots_repo.replace_days(conn, "piscina", 58, [d1], [], t0 + timedelta(minutes=5))  # d1 ya sin turnos

    assert [s.day for s in slots_repo.list_slots(conn, "piscina", 58, [d1, d2])] == [d2]
    assert slots_repo.oldest_fetch(conn, "piscina", 58, [d1, d2]) == t0
    assert slots_repo.oldest_fetch(conn, "piscina", 58, [d1, d2, date(2026, 10, 3)]) is None


def test_refresh_is_skipped_if_recent(monkeypatch, conn, settings):
    calls = []
    monkeypatch.setattr(portal, "fetch_slots", lambda *a: calls.append(a) or [])
    days = [date(2026, 10, 1)]
    now = datetime(2026, 9, 30, 12, 0, tzinfo=MADRID)

    assert core_slots.refresh_slots(conn, settings, 1, "piscina", 58, days, now) is True
    assert core_slots.refresh_slots(conn, settings, 1, "piscina", 58, days, now + timedelta(seconds=30)) is False
    assert core_slots.refresh_slots(conn, settings, 1, "piscina", 58, days, now + timedelta(minutes=2)) is True
    assert len(calls) == 2


def test_needs_refresh_when_old_or_missing(conn):
    days = [date(2026, 10, 1)]
    now = datetime(2026, 9, 30, 12, 0, tzinfo=MADRID)
    assert core_slots.needs_refresh(conn, "piscina", 58, days, now)
    slots_repo.replace_days(conn, "piscina", 58, days, [], now)
    assert not core_slots.needs_refresh(conn, "piscina", 58, days, now + timedelta(minutes=5))
    assert core_slots.needs_refresh(conn, "piscina", 58, days, now + timedelta(minutes=11))
