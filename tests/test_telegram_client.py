"""TelegramClient de verdad, con la red simulada (urlopen), para cazar fallos como el de «timeout» duplicado."""

import io
import json

import pytest

from src.notify import telegram
from src.notify.telegram import CallbackQuery, StartMessage, TelegramClient


@pytest.fixture
def api(monkeypatch):
    """Sustituye la red: guarda cada petición y devuelve la respuesta preparada."""
    # conftest bloquea _call en todos los tests; aquí queremos el _call real
    monkeypatch.setattr(TelegramClient, "_call", ORIGINAL_CALL)
    calls, responses = [], []

    def fake_urlopen(request, timeout):
        calls.append({"url": request.full_url, "body": json.loads(request.data), "http_timeout": timeout})
        return io.BytesIO(json.dumps({"ok": True, "result": responses.pop(0)}).encode())

    monkeypatch.setattr(telegram.urllib.request, "urlopen", fake_urlopen)
    return calls, responses


ORIGINAL_CALL = TelegramClient._call


def test_get_updates_long_polling(api):
    calls, responses = api
    responses.append([
        {"update_id": 7, "message": {"text": "/start abc", "chat": {"id": 111, "type": "private", "first_name": "A"}}},
        {"update_id": 8, "callback_query": {"id": "cb1", "data": "obs:3", "message": {"message_id": 5, "chat": {"id": 111}}}},
        {"update_id": 9, "message": {"text": "hola", "chat": {"id": 111, "type": "private"}}},
    ])
    updates, offset = TelegramClient("123:abc").get_updates(offset=6, wait_s=25)

    assert updates == [StartMessage(7, 111, "abc", "A"), CallbackQuery(8, "cb1", 111, 5, "obs:3")]
    assert offset == 10
    assert calls[0]["body"]["timeout"] == 25  # parámetro de Telegram
    assert calls[0]["http_timeout"] == 25 + telegram.TIMEOUT_S  # espera de la petición
    assert calls[0]["body"]["offset"] == 6


def test_send_message_with_buttons(api):
    calls, responses = api
    responses.append({"message_id": 1})
    TelegramClient("123:abc").send_message(111, "hola", [("👀 Observar", "obs:3")])
    body = calls[0]["body"]
    assert body["reply_markup"]["inline_keyboard"] == [[{"text": "👀 Observar", "callback_data": "obs:3"}]]


def test_token_never_in_error_message(monkeypatch):
    monkeypatch.setattr(TelegramClient, "_call", ORIGINAL_CALL)

    def failing(request, timeout):
        raise telegram.urllib.error.URLError("sin red")

    monkeypatch.setattr(telegram.urllib.request, "urlopen", failing)
    with pytest.raises(telegram.TelegramError) as info:
        TelegramClient("123:secreto").bot_username()
    assert "secreto" not in str(info.value)
