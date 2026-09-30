"""Turnos (horas y plazas libres) de un centro para varios días, en una sola visita. Solo lectura."""

from dataclasses import dataclass
from datetime import date, time

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from src.config import Settings
from src.portal import selectors as sel
from src.portal.auth import session_is_valid
from src.portal.browser import describe_error, goto, open_context, session_file
from src.portal.centers import open_service
from src.portal.errors import PortalError, SessionExpired

DAY_CHANGE_SETTLE_MS = 800


@dataclass(frozen=True)
class PortalSlot:
    day: date
    time: time
    activity: str
    free: int  # plazas libres ahora (si el turno aún no abre, coincide con el aforo)
    total: int  # aforo
    selectable: bool  # False si el portal lo tacha (sin plazas o ya pasado)


Target = tuple[str, int]  # (servicio, facility_code)


def fetch_slots(settings: Settings, profile_id: int, service: str, portal_id: int, days: list[date]) -> list[PortalSlot]:
    """Turnos de un centro para cada día pedido. Lanza SessionExpired si la sesión no vale."""
    result = fetch_slots_many(settings, profile_id, [(service, portal_id)], days)[(service, portal_id)]
    if isinstance(result, PortalError):
        raise result
    return result


def fetch_slots_many(
    settings: Settings, profile_id: int, targets: list[Target], days: list[date]
) -> dict[Target, list[PortalSlot] | PortalError]:
    """Varios centros en una sola visita (un navegador, una comprobación de sesión).

    Un fallo en un centro no impide leer los demás: su resultado es el PortalError.
    Lanza SessionExpired si la sesión no vale.
    """
    state = session_file(settings, profile_id)
    if not state.exists():
        raise SessionExpired("No hay sesión guardada para este perfil.")
    results: dict[Target, list[PortalSlot] | PortalError] = {}
    try:
        with open_context(settings, state) as context:
            page = context.new_page()
            if not session_is_valid(page):  # deja la página en Home
                raise SessionExpired("La sesión guardada ha caducado.")
            for i, (service, portal_id) in enumerate(targets):
                if i > 0:
                    goto(page, sel.HOME_URL)  # el token de AltaEventos cambia: volver a entrar desde Home
                try:
                    results[(service, portal_id)] = _read_center_week(page, service, portal_id, days)
                except PortalError as exc:
                    results[(service, portal_id)] = exc
                except PlaywrightError as exc:
                    results[(service, portal_id)] = PortalError(describe_error(exc))
    except PlaywrightError as exc:
        raise PortalError(describe_error(exc)) from None
    return results


def _read_center_week(page: Page, service: str, portal_id: int, days: list[date]) -> list[PortalSlot]:
    open_service(page, service)
    open_center(page, portal_id)
    slots: list[PortalSlot] = []
    for day in days:
        select_day(page, day)
        slots.extend(read_slots(page, day))
    return slots


def open_center(page: Page, portal_id: int) -> None:
    card = page.locator(sel.CENTER_CARD_BY_ID.format(portal_id=portal_id))
    if card.count() == 0:
        raise PortalError("Ese polideportivo ya no aparece en la lista del portal.")
    card.first.click()
    page.locator(sel.SLOTS_DATEPICKER).wait_for()
    page.wait_for_load_state("networkidle")


def select_day(page: Page, day: date) -> None:
    """Pulsa el día en el calendario y espera a que el portal sustituya la lista de turnos."""
    key = day.strftime("%d/%m/%Y")
    active = page.locator(sel.DATEPICKER_ACTIVE_DAY)
    if active.count() and active.first.get_attribute("data-day") == key:
        return

    cell = page.locator(sel.DATEPICKER_DAY.format(day=key))
    if cell.count() == 0:  # el día está en el mes siguiente del calendario
        page.locator(sel.DATEPICKER_NEXT).first.click()
        cell = page.locator(sel.DATEPICKER_DAY.format(day=key))
    if cell.count() == 0:
        raise PortalError(f"El calendario del portal no ofrece el día {key}.")

    old_list = page.locator(sel.SLOT_LISTS).first.element_handle() if page.locator(sel.SLOT_LISTS).count() else None
    cell.first.click()
    if old_list is not None:
        # El UpdatePanel sustituye el HTML: la lista anterior deja de estar en el documento
        page.wait_for_function("el => !el.isConnected", arg=old_list)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(DAY_CHANGE_SETTLE_MS)


def read_slots(page: Page, day: date) -> list[PortalSlot]:
    blocks = page.locator(sel.SLOT_LISTS).evaluate_all(sel.EXTRACT_SLOTS_JS)
    slots = []
    for block in blocks:
        for raw in block["slots"]:
            try:
                hour = time.fromisoformat(raw["time"])
            except ValueError:
                continue
            if raw["free"] is None or raw["total"] is None:
                continue
            slots.append(
                PortalSlot(day, hour, block["activity"], int(raw["free"]), int(raw["total"]), not raw["struck"])
            )
    return slots
