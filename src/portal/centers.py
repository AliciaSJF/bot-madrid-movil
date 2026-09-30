"""Lista de polideportivos de un servicio de uso libre (solo lectura)."""

from dataclasses import dataclass

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from src.config import Settings
from src.portal import selectors as sel
from src.portal.auth import session_is_valid
from src.portal.browser import describe_error, open_context, session_file
from src.portal.errors import PortalError, SessionExpired


@dataclass(frozen=True)
class PortalCenter:
    portal_id: int
    name: str
    address: str
    favorite: bool  # favorito en la cuenta del portal (el mismo que en Madrid Móvil)


def fetch_centers(settings: Settings, profile_id: int, service: str) -> list[PortalCenter]:
    """Abre la lista del servicio con la sesión guardada del perfil y la devuelve.

    Lanza SessionExpired si la sesión no vale (quien llama decide si inicia sesión otra vez).
    """
    if service not in sel.SERVICE_CARD_TITLES:
        raise ValueError(f"Servicio desconocido: {service}")
    state = session_file(settings, profile_id)
    if not state.exists():
        raise SessionExpired("No hay sesión guardada para este perfil.")
    try:
        with open_context(settings, state) as context:
            page = context.new_page()
            if not session_is_valid(page):  # deja la página en Home
                raise SessionExpired("La sesión guardada ha caducado.")
            open_service(page, service)
            return read_centers(page)
    except PlaywrightError as exc:
        raise PortalError(describe_error(exc)) from None


def open_service(page: Page, service: str) -> None:
    """Desde Home, pulsa la tarjeta del servicio y espera a que carguen los centros."""
    title = sel.SERVICE_CARD_TITLES[service]
    page.locator(sel.HOME_CARD.format(title=title)).first.click()
    page.locator(sel.CENTER_CARD).first.wait_for()


def read_centers(page: Page) -> list[PortalCenter]:
    raw = page.locator(sel.CENTER_CARD).evaluate_all(sel.EXTRACT_CENTERS_JS)
    centers = [
        PortalCenter(portal_id=c["id"], name=c["name"], address=c["address"], favorite=c["favorite"])
        for c in raw
        if c["id"] is not None and c["name"]
    ]
    if not centers:
        raise PortalError("El portal no devolvió ningún polideportivo.")
    return centers
