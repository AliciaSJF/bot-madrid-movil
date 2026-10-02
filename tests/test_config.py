import pytest
from pydantic import ValidationError

from src.config import Settings


def test_defaults_are_safe(monkeypatch):
    for var in ("WATCH_MIN_INTERVAL_S", "TZ"):
        monkeypatch.delenv(var, raising=False)
    settings = Settings(_env_file=None)
    assert settings.watch_min_interval_s == 30
    assert settings.zone.key == "Europe/Madrid"


def test_watch_interval_below_minimum_is_rejected():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, watch_min_interval_s=10)


def test_secrets_are_not_printed():
    settings = Settings(_env_file=None, fernet_key="super-secreta")
    assert "super-secreta" not in str(settings.model_dump())
