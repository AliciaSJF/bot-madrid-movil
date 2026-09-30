"""Navegador bajo demanda: se abre para una tarea y se cierra al terminar (la Pi tiene poca RAM)."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from src.config import Settings

DEFAULT_TIMEOUT_MS = 20_000
NETWORK_RETRY_DELAY_MS = 2_000


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


def goto(page: Page, url: str, wait_until: str = "domcontentloaded") -> None:
    """Navega reintentando UNA vez si falla la red (p. ej. ERR_NETWORK_CHANGED al cambiar de adaptador).

    Solo para cargar páginas: nunca se usa para reenviar un formulario.
    """
    try:
        page.goto(url, wait_until=wait_until)
    except PlaywrightError as exc:
        if "net::ERR_" not in str(exc):
            raise
        page.wait_for_timeout(NETWORK_RETRY_DELAY_MS)
        page.goto(url, wait_until=wait_until)


def describe_error(exc: PlaywrightError, *secrets: str) -> str:
    """Mensaje corto y legible de un error de Playwright, sin datos sensibles."""
    first_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
    for secret in secrets:
        if secret:
            first_line = first_line.replace(secret, "***")
    if "net::ERR_" in first_line:
        code = first_line.split("net::", 1)[1].split()[0]
        return f"Problema de red al abrir el portal ({code}). Vuelve a probar en un momento."
    if "Timeout" in first_line:
        return "El portal tardó demasiado en responder. Vuelve a probar en un momento."
    return f"Fallo del navegador: {first_line[:160]}"
