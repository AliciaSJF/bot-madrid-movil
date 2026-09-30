"""Protecciones comunes: ningún test puede hablar con el portal real."""

import pytest

from src.core import prefetch
from src.worker import scheduler
from src.notify.telegram import TelegramClient


class RealPortalAccess(RuntimeError):
    pass


class RealTelegramAccess(RuntimeError):
    pass


def _no_telegram(*_args, **_kwargs):
    raise RealTelegramAccess("Un test ha intentado llamar a la API real de Telegram")


def _forbidden(*_args, **_kwargs):
    raise RealPortalAccess("Un test ha intentado abrir el navegador contra el portal real")


@pytest.fixture(autouse=True)
def no_real_portal(monkeypatch):
    monkeypatch.setattr(prefetch, "ENABLED", False)
    monkeypatch.setattr(scheduler, "ENABLED", False)
    # open_context es la única puerta al portal real; se importa por nombre en cada módulo
    for module in (
        "src.portal.browser", "src.portal.auth", "src.portal.centers", "src.portal.slots",
        "src.portal.capture", "src.portal.booking", "src.portal.cancel",
    ):
        monkeypatch.setattr(f"{module}.open_context", _forbidden)
    # Toda llamada a Telegram pasa por TelegramClient._call
    monkeypatch.setattr(TelegramClient, "_call", _no_telegram)
