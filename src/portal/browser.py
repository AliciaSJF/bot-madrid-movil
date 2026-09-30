"""Navegador bajo demanda: se abre para una tarea y se cierra al terminar (la Pi tiene poca RAM)."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import BrowserContext, sync_playwright

from src.config import Settings

DEFAULT_TIMEOUT_MS = 20_000


def session_file(settings: Settings, profile_id: int) -> Path:
    """storage_state de Playwright (cookies) de un perfil. Contiene la sesión: nunca al repo."""
    return settings.sessions_dir / f"{profile_id}.json"


@contextmanager
def open_context(settings: Settings, storage_state: Path | None = None) -> Iterator[BrowserContext]:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=settings.headless)
        try:
            context = browser.new_context(
                storage_state=storage_state if storage_state and storage_state.exists() else None,
                locale="es-ES",
                timezone_id=settings.tz,
                viewport={"width": 1280, "height": 900},
            )
            context.set_default_timeout(DEFAULT_TIMEOUT_MS)
            yield context
        finally:
            browser.close()
