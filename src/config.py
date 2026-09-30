"""Ajustes de la aplicación, leídos de variables de entorno y de .env."""

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Clave Fernet para las credenciales del portal; nunca en la BD ni en el repo
    fernet_key: SecretStr | None = None

    data_dir: Path = Path("data")
    tz: str = "Europe/Madrid"

    # Por defecto nunca se confirma una reserva real
    dry_run: bool = True

    # Respeto al portal: la vigilancia nunca consulta más a menudo que esto
    watch_min_interval_s: int = Field(default=30, ge=30)
    max_active_watches: int = Field(default=3, ge=1)
    # Cuánto sigue intentando una reserva tras la apertura si el portal no responde (entre semana se cae)
    booking_window_s: int = Field(default=600, ge=60, le=3600)

    headless: bool = True
    # Navegador instalado a usar en vez del Chromium de Playwright (p. ej. "msedge" en Windows).
    # Vacío = Chromium de Playwright, que es lo que se usa en la Pi.
    browser_channel: str | None = None

    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    @property
    def sessions_dir(self) -> Path:
        """Carpeta con el storage_state de Playwright de cada usuario."""
        return self.data_dir / "sessions"


@lru_cache
def get_settings() -> Settings:
    return Settings()
