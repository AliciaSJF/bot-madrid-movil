"""Anular en el portal una reserva de uso libre ya hecha (Mi cuenta → Entradas de uso libre).

Camino verificado con una anulación real el 2026-09-30:
    Mi cuenta → «Entradas de uso libre» → fila (fecha y hora) → «Consultar» → «Anular» → «Sí»
El importe vuelve al monedero al momento. Solo se anula la fila que coincide en fecha, hora y centro.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

from src.config import Settings
from src.portal import selectors as sel
from src.portal.auth import session_is_valid
from src.portal.browser import describe_error, goto, open_context, session_file
from src.portal.errors import PortalError, SessionExpired

MAX_PAGES = 5
CANCEL_WAIT_MS = 4_000


@dataclass(frozen=True)
class CancelResult:
    status: str  # "anulado" | "ya_anulado" | "no_encontrado" | "no_permitido"
    message: str
    wallet_balance: Decimal | None = None


def _plain(text: str) -> str:
    """Sin tildes ni mayúsculas: el portal escribe «Juan de Dios Roman» y la lista de centros «Román»."""
    return unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode().lower()


def cancel_reservation(settings: Settings, profile_id: int, day: date, start: time, center: str) -> CancelResult:
    state = session_file(settings, profile_id)
    if not state.exists():
        raise SessionExpired("No hay sesión guardada para este perfil.")
    try:
        with open_context(settings, state) as context:
            page = context.new_page()
            if not session_is_valid(page):
                raise SessionExpired("La sesión guardada ha caducado.")
            _open_account_card(page, sel.ACCOUNT_FREE_USE_CARD)
            row = _find_row(page, day, start, center)
            if row is None:
                return CancelResult("no_encontrado", "No encuentro esa reserva en «Entradas de uso libre».")
            if sel.CANCELLED_TEXT in row.inner_text():
                return CancelResult("ya_anulado", "La reserva ya estaba anulada en el portal.")

            row.locator(sel.RESERVATION_CONSULT).first.evaluate("button => button.click()")
            cancel = page.locator(sel.CANCEL_BUTTON)
            cancel.wait_for()
            if not cancel.is_enabled():
                return CancelResult("no_permitido", "El portal no permite anular esta reserva (¿fuera de plazo?).")
            cancel.click()
            page.locator(sel.CANCEL_CONFIRM_YES).first.wait_for()
            page.locator(sel.CANCEL_CONFIRM_YES).first.click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(CANCEL_WAIT_MS)

            # Comprobar en la lista que ha quedado anulada
            goto(page, sel.HOME_URL)
            _open_account_card(page, sel.ACCOUNT_FREE_USE_CARD)
            row = _find_row(page, day, start, center)
            if row is None or sel.CANCELLED_TEXT not in row.inner_text():
                return CancelResult("no_permitido", "He pulsado «Anular», pero la reserva no aparece como anulada.")

            balance = _wallet_balance(page)
            return CancelResult("anulado", "Anulada en el portal.", balance)
    except PlaywrightError as exc:
        raise PortalError(describe_error(exc)) from None


def _open_account_card(page: Page, title: str) -> None:
    page.locator(sel.ACCOUNT_LINK).first.click()
    page.wait_for_url(f"**{sel.ACCOUNT_PATH}**")
    card = page.locator(sel.ACCOUNT_CARD.format(title=title)).first
    card.wait_for()
    card.click()
    page.wait_for_load_state("networkidle")
    page.locator(sel.RESERVATION_ROWS).first.wait_for()


def _find_row(page: Page, day: date, start: time, center: str) -> Locator | None:
    """Fila con esa fecha y hora de inicio (y ese centro, si hay varias). Mira hasta MAX_PAGES páginas."""
    wanted_center = _plain(center)
    wanted_day = day.strftime("%d/%m/%Y")
    for _ in range(MAX_PAGES):
        rows = page.locator(sel.RESERVATION_ROWS).filter(has_text=wanted_day)
        matches = []
        for i in range(rows.count()):
            cells = [c.strip() for c in rows.nth(i).locator("td").all_inner_texts()]
            if wanted_day not in cells:
                continue
            # Columnas: …, fecha, día de la semana, hora de inicio, hora de fin, …
            start_cell = cells.index(wanted_day) + 2
            if start_cell < len(cells) and cells[start_cell] == start.strftime("%H:%M"):
                matches.append((rows.nth(i), _plain(" ".join(cells))))
        by_center = [row for row, text in matches if wanted_center in text]
        if len(by_center) == 1:
            return by_center[0]
        if len(matches) == 1 and not by_center:
            return matches[0][0]
        if matches:
            return None  # varias filas iguales y ninguna clara: mejor no anular nada
        next_page = page.locator(sel.TABLE_NEXT_PAGE)
        if next_page.count() == 0:
            return None
        next_page.first.click()
        page.wait_for_load_state("networkidle")
    return None


def _wallet_balance(page: Page) -> Decimal | None:
    try:
        goto(page, sel.HOME_URL)
        page.locator(sel.ACCOUNT_LINK).first.click()
        page.wait_for_url(f"**{sel.ACCOUNT_PATH}**")
        card = page.locator(sel.ACCOUNT_CARD.format(title=sel.ACCOUNT_WALLET_CARD)).first
        card.wait_for()
        card.click()
        page.wait_for_load_state("networkidle")
        page.get_by_text("Saldo actual").first.wait_for()
        match = sel.WALLET_BALANCE.search(re.sub(r"\s+", " ", page.locator("body").inner_text()))
    except PlaywrightError:
        return None  # el saldo es un extra del aviso: si no se lee, no pasa nada
    return Decimal(match.group(1).replace(".", "").replace(",", ".")) if match else None
