from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.config import Settings
from src.db import crypto
from src.db import profiles as profiles_repo
from src.db.database import connect, init_db
from src.notify import linking, notify
from src.notify.telegram import StartMessage

MADRID = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=MADRID)


class FakeTelegram:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.pending: list[StartMessage] = []
        self.offsets: list[int | None] = []

    def bot_username(self):
        return "deportes_casa_bot"

    def send_message(self, chat_id, text, buttons=None):
        self.sent.append((chat_id, text))

    def get_updates(self, offset, wait_s=0):
        self.offsets.append(offset)
        starts, self.pending = self.pending, []
        return starts, (starts[-1].update_id + 1 if starts else offset)


@pytest.fixture
def fake(monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setattr(linking, "client", lambda _settings: fake)
    return fake


@pytest.fixture
def settings(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path, fernet_key=Fernet.generate_key().decode(),
                 telegram_bot_token="123:abc")
    init_db(s.data_dir)
    return s


@pytest.fixture
def conn(settings):
    c = connect(settings.data_dir)
    for name in ("Alicia", "Pareja"):
        profiles_repo.create_profile(c, name, "#0f766e", f"{name}@example.com", crypto.encrypt(settings, "x"))
    c.commit()
    yield c
    c.close()


def code_of(link: str) -> str:
    return link.split("start=")[1]


def test_start_link_builds_bot_link_with_one_time_code(conn, settings, fake):
    link = linking.start_link(conn, settings, 1, NOW)
    assert link.startswith("https://t.me/deportes_casa_bot?start=")
    assert profiles_repo.find_by_link_code(conn, code_of(link), NOW) == 1


def test_each_profile_links_its_own_chat(conn, settings, fake):
    code_a = code_of(linking.start_link(conn, settings, 1, NOW))
    code_b = code_of(linking.start_link(conn, settings, 2, NOW))
    fake.pending = [StartMessage(10, 111, code_a, "A"), StartMessage(11, 222, code_b, "B")]

    assert linking.process_updates(conn, settings, NOW) == [1, 2]
    assert profiles_repo.get_profile(conn, 1).telegram_chat_id == 111
    assert profiles_repo.get_profile(conn, 2).telegram_chat_id == 222
    assert [chat for chat, _ in fake.sent] == [111, 222]  # confirmación a cada uno

    # El código ya no vale y las actualizaciones quedan marcadas como leídas
    assert profiles_repo.find_by_link_code(conn, code_a, NOW) is None
    linking.process_updates(conn, settings, NOW)
    assert fake.offsets[-1] == 12


def test_expired_or_unknown_code_does_not_link(conn, settings, fake):
    code = code_of(linking.start_link(conn, settings, 1, NOW))
    fake.pending = [StartMessage(10, 111, code, "A"), StartMessage(11, 333, "", "X")]

    assert linking.process_updates(conn, settings, NOW + timedelta(minutes=16)) == []
    assert profiles_repo.get_profile(conn, 1).telegram_chat_id is None
    assert len(fake.sent) == 2  # a los dos se les explica cómo vincular


def test_notify_only_goes_to_that_profile(conn, settings, fake):
    profiles_repo.set_telegram_chat(conn, 1, 111)
    assert notify(conn, settings, 1, "Reserva conseguida") is True
    assert notify(conn, settings, 2, "Reserva conseguida") is False  # sin vincular
    assert fake.sent == [(111, "Reserva conseguida")]


def test_account_page_flow(conn, settings, fake):
    client = TestClient(create_app(settings), follow_redirects=False)
    client.post("/configurar", data={"password": "casa-segura-123", "password2": "casa-segura-123"})
    client.post("/perfiles/1/elegir")
    assert "Vincular Telegram" in client.get("/perfil").text

    client.post("/perfil/telegram/vincular")
    page = client.get("/perfil").text
    assert "https://t.me/deportes_casa_bot?start=" in page

    conn = connect(settings.data_dir)
    code = conn.execute("SELECT telegram_link_code FROM profiles WHERE id = 1").fetchone()[0]
    conn.close()
    fake.pending = [StartMessage(10, 111, code, "A")]
    assert client.post("/perfil/telegram/comprobar").json() == {"linked": True}
    assert "Enviar mensaje de prueba" in client.get("/perfil").text

    client.post("/perfil/telegram/prueba")
    assert fake.sent[-1][0] == 111


def test_account_page_without_token(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path, fernet_key=Fernet.generate_key().decode())
    client = TestClient(create_app(s), follow_redirects=False)
    client.post("/configurar", data={"password": "casa-segura-123", "password2": "casa-segura-123"})
    client.post("/perfiles/nuevo", data={"display_name": "A", "color": "#0f766e", "portal_username": "a", "portal_password": "b"})
    assert "Falta <code>TELEGRAM_BOT_TOKEN</code>" in client.get("/perfil").text
