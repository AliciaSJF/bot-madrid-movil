"""Reservar un turno: preparar la página, pulsar el turno a la hora exacta y pagar con el monedero.

Uso (desde src/core/booking.py):

    with BookingSession(settings, profile_id) as session:
        session.start(username, password)        # sesión válida o login (1 min antes)
        session.prepare(service, center_id, day)  # centro y día ya abiertos
        ...esperar a la hora exacta...
        outcome = session.try_add_to_cart(at, activity)   # repetir si hace falta
        cart = session.read_cart(day, at)
        result = session.confirm_with_wallet(cart)         # ÚNICO paso que compra

Reglas: solo se paga con Monedero; nunca se toca tarjeta ni Bizum; si el carrito trae algo más
que este turno, si pide aceptar condiciones o si sale del portal hacia una pasarela de pago, se para.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from decimal import Decimal
from pathlib import Path

from playwright.sync_api import BrowserContext, Page
from playwright.sync_api import Error as PlaywrightError

from src.config import Settings
from src.portal import selectors as sel
from src.portal.auth import login, session_is_valid
from src.portal.browser import describe_error, goto, open_context, session_file
from src.portal.centers import open_service
from src.portal.errors import PortalError
from src.portal.slots import open_center, select_day

# Observado el 2026-09-30: en el minuto de apertura el portal tardó 28 s en responder.
# Mientras procesa un clic no se vuelve a pulsar (sería mandar otra petición encima).
CLICK_RESULT_TIMEOUT_S = 45
# Si el portal no está procesando nada y no ha cambiado la página en este tiempo, el clic no hizo nada
CLICK_IDLE_S = 1.5
# Observado el 2026-09-30 18:00: tras el clic la lista de turnos desaparece un rato mientras el
# portal la redibuja. Antes de decidir nada se espera a que vuelva.
LIST_READY_TIMEOUT_S = 20
# Esperas por acción en la sesión de reserva (el portal saturado tarda mucho más que los 20 s normales)
BOOKING_ACTION_TIMEOUT_MS = 45_000
IN_ASYNC_POSTBACK_JS = """() => {
  try { return !!Sys.WebForms.PageRequestManager.getInstance().get_isInAsyncPostBack(); }
  catch (e) { return false; }
}"""
CONFIRM_RESULT_TIMEOUT_S = 30
MONTHS_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
             "septiembre", "octubre", "noviembre", "diciembre"]
MONEY = re.compile(r"(\d{1,4}(?:\.\d{3})*,\d{2})\s*€")


@dataclass(frozen=True)
class SlotState:
    found: bool
    selectable: bool  # sin tachar y con enlace
    free: int | None
    total: int | None


@dataclass(frozen=True)
class AddOutcome:
    in_cart: bool
    message: str  # aviso del portal si no se añadió (vacío si no hubo)
    page_broken: bool = False  # la página no tiene la lista de turnos: hay que volver a entrar


@dataclass(frozen=True)
class Cart:
    items: int
    matches_slot: bool  # el único elemento es este turno (fecha y hora)
    total: Decimal | None
    wallet_balance: Decimal | None  # None si no aparece la opción Monedero
    needs_terms: bool


@dataclass(frozen=True)
class ConfirmResult:
    status: str  # "reservado" | "fallido" | "revisar" | "pasarela"
    message: str
    capture: Path | None


def parse_money(text: str) -> Decimal | None:
    match = MONEY.search(text or "")
    if not match:
        return None
    return Decimal(match.group(1).replace(".", "").replace(",", "."))


def spanish_date(day: date) -> str:
    """Como lo escribe el carrito: «1 de octubre de 2026» (sin el día de la semana)."""
    return f"{day.day} de {MONTHS_ES[day.month - 1]} de {day.year}"


class BookingSession:
    def __init__(self, settings: Settings, profile_id: int, capture_dir: Path | None = None):
        self.settings = settings
        self.profile_id = profile_id
        self.capture_dir = capture_dir
        self._cm = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.day: date | None = None

    # --- ciclo de vida -------------------------------------------------------

    def __enter__(self) -> "BookingSession":
        state = session_file(self.settings, self.profile_id)
        state.parent.mkdir(parents=True, exist_ok=True)
        self._cm = open_context(self.settings, state, exclusive=False)
        self.context = self._cm.__enter__()
        self.context.set_default_timeout(BOOKING_ACTION_TIMEOUT_MS)
        self.page = self.context.new_page()
        return self

    def __exit__(self, *exc) -> None:
        self._cm.__exit__(*exc)

    def _guard(self, action):
        try:
            return action()
        except PlaywrightError as exc:
            raise PortalError(describe_error(exc)) from None

    # --- preparación (antes de la hora) ----------------------------------------

    def start(self, username: str, password: str) -> bool:
        """Deja la sesión iniciada. Devuelve True si hizo falta hacer login."""

        def run() -> bool:
            if session_is_valid(self.page):
                return False
            login(self.page, username, password)
            self.context.storage_state(path=session_file(self.settings, self.profile_id))
            return True

        try:
            return run()
        except PlaywrightError as exc:  # el mensaje podría incluir lo escrito: se tapa
            raise PortalError(describe_error(exc, username, password)) from None

    def prepare(self, service: str, center_id: int, day: date) -> None:
        """Abre el centro y el día del turno, listo para pulsar."""

        def run() -> None:
            if sel.HOME_PATH not in self.page.url:
                goto(self.page, sel.HOME_URL)
            open_service(self.page, service)
            open_center(self.page, center_id)
            select_day(self.page, day)

        self._guard(run)
        self.day = day

    def recover(self, service: str, center_id: int, day: date) -> None:
        """Vuelve a entrar desde el inicio hasta el día del turno (si la página quedó rota)."""
        self._guard(lambda: goto(self.page, sel.HOME_URL))
        self.prepare(service, center_id, day)

    def capture(self, name: str) -> Path | None:
        return self._capture(name)

    def refresh_day(self) -> None:
        """Vuelve a pedir la lista de turnos del día (otro día y vuelta, que es lo que hace el portal)."""

        def run() -> None:
            neighbor = self.day + timedelta(days=1)
            if self.page.locator(sel.DATEPICKER_DAY.format(day=neighbor.strftime("%d/%m/%Y"))).count() == 0:
                neighbor = self.day - timedelta(days=1)
            select_day(self.page, neighbor)
            select_day(self.page, self.day)

        self._guard(run)

    # --- el turno ------------------------------------------------------------

    def _slot_link(self, at: time, activity: str):
        """Localiza el <li> del turno por actividad y hora. Devuelve (li, estado)."""
        lists = self.page.locator(sel.SLOT_LISTS)
        blocks = lists.evaluate_all(sel.EXTRACT_SLOTS_JS)
        hour = at.strftime("%H:%M")
        for b, block in enumerate(blocks):
            if activity and block["activity"] != activity:
                continue
            for i, raw in enumerate(block["slots"]):
                if raw["time"] == hour:
                    li = lists.nth(b).locator("li.media").nth(i)
                    selectable = not raw["struck"] and li.locator("a[href]").count() > 0
                    return li, SlotState(True, selectable, raw["free"], raw["total"])
        return None, SlotState(False, False, None, None)

    def slot_state(self, at: time, activity: str) -> SlotState:
        return self._guard(lambda: self._slot_link(at, activity)[1])

    def try_add_to_cart(self, at: time, activity: str) -> AddOutcome:
        """Pulsa el turno una vez y espera a ver qué pasa. No compra nada todavía."""

        def run() -> AddOutcome:
            if not self._wait_list_ready():
                return AddOutcome(False, "La lista de turnos no ha vuelto a cargar.", page_broken=True)
            li, state = self._slot_link(at, activity)
            if li is None:
                return AddOutcome(False, "El turno no aparece en la lista del portal.", page_broken=True)
            if not state.selectable:
                return AddOutcome(False, "El portal muestra el turno sin plazas.")
            # Sin esperar a que la página termine de cambiar: si no, con el portal saturado el bot se
            # queda ciego hasta que responde (24 s el 30/09). Lo que pasa se vigila en el bucle.
            li.locator("a").first.click(no_wait_after=True)
            clicked = datetime.now()
            deadline = clicked + timedelta(seconds=CLICK_RESULT_TIMEOUT_S)
            while datetime.now() < deadline:
                if sel.CART_PATH in self.page.url:
                    self.page.wait_for_load_state("domcontentloaded")
                    self.page.locator(sel.CART_CONFIRM_BUTTON).wait_for()
                    return AddOutcome(True, "")
                alerts = self._visible_alerts()
                if alerts:
                    return AddOutcome(False, re.sub(r"\s+", " ", alerts[0])[:200])
                busy = self._in_postback()
                if not busy and (datetime.now() - clicked).total_seconds() > CLICK_IDLE_S:
                    return AddOutcome(False, "El portal no hizo nada al pulsar el turno.")
                self.page.wait_for_timeout(100)
            return AddOutcome(False, f"El portal no respondió en {CLICK_RESULT_TIMEOUT_S} s.")

        return self._guard(run)

    def open_cart(self) -> bool:
        """Abre el carrito desde el enlace de la cabecera. Devuelve False si no se pudo."""

        def run() -> bool:
            link = self.page.locator(sel.HEADER_CART_LINK)
            if link.count() == 0:
                return False
            link.first.click()
            self.page.wait_for_url(f"**{sel.CART_PATH}**")
            self.page.locator(sel.CART_CONFIRM_BUTTON).wait_for()
            return True

        return self._guard(run)

    def _wait_list_ready(self) -> bool:
        """Espera a que el portal no esté procesando nada y la lista de turnos esté en la página."""
        deadline = datetime.now() + timedelta(seconds=LIST_READY_TIMEOUT_S)
        while datetime.now() < deadline:
            try:
                if not self._in_postback() and self.page.locator(sel.SLOT_LISTS).count() > 0:
                    return True
            except PlaywrightError:
                pass  # navegando: seguir esperando
            self.page.wait_for_timeout(200)
        return False

    def _visible_alerts(self) -> list[str]:
        """Avisos visibles. Si la página está cambiando (p. ej. hacia el carrito o el resultado),
        no hay nada que leer todavía: no es un error."""
        try:
            return [t.strip() for t in self.page.locator(sel.VISIBLE_ALERTS).all_inner_texts() if t.strip()]
        except PlaywrightError:
            return []

    def _in_postback(self) -> bool:
        try:
            return bool(self.page.evaluate(IN_ASYNC_POSTBACK_JS))
        except PlaywrightError:  # la página está navegando: sigue ocupado
            return True

    def server_clock_offset(self) -> float | None:
        """Segundos que va adelantado el reloj del portal respecto al nuestro (cabecera Date, ±0,5 s)."""
        try:
            sent = datetime.now(UTC)
            response = self.context.request.head(sel.HOME_URL)
            received = datetime.now(UTC)
            server = parsedate_to_datetime(response.headers["date"])
        except (PlaywrightError, KeyError, TypeError, ValueError):
            return None
        middle = sent + (received - sent) / 2
        return (server - middle).total_seconds() + 0.5  # la cabecera trunca al segundo

    # --- carrito y pago ------------------------------------------------------

    def read_cart(self, day: date, at: time) -> Cart:
        def run() -> Cart:
            items = self.page.locator(sel.CART_ITEMS)
            count = items.count()
            matches = False
            if count == 1:
                text = re.sub(r"\s+", " ", items.first.inner_text())
                matches = spanish_date(day) in text and at.strftime("%H:%M") in text
            total = parse_money(self.page.locator(sel.CART_TOTAL).first.inner_text()) if self.page.locator(sel.CART_TOTAL).count() else None
            wallet = self.page.locator(sel.PAYMENT_METHODS).filter(has_text=sel.PAYMENT_WALLET_TEXT)
            balance = parse_money(wallet.first.inner_text()) if wallet.count() else None
            terms = self.page.locator(f"{sel.CART_TERMS} input[type=checkbox]")
            return Cart(count, matches, total, balance, terms.count() > 0)

        return self._guard(run)

    def confirm_with_wallet(self, cart: Cart) -> ConfirmResult:
        """Marca Monedero y pulsa «Confirmar la compra». ESTE ES EL PASO QUE COMPRA.

        Quien llama ya ha comprobado el carrito (un solo elemento, saldo suficiente, sin condiciones).
        """

        def run() -> ConfirmResult:
            wallet = self.page.locator(sel.PAYMENT_METHODS).filter(has_text=sel.PAYMENT_WALLET_TEXT)
            wallet.first.locator("input[type=radio]").check()
            self.page.wait_for_load_state("networkidle")
            self.page.locator(sel.CART_CONFIRM_BUTTON).click()
            # A partir de aquí la compra puede estar hecha: ningún fallo al leer la página es "fallido"
            try:
                return after_click()
            except PlaywrightError as exc:
                capture = self._capture("error_tras_confirmar")
                return ConfirmResult("revisar", f"Tras confirmar no pude leer la página ({describe_error(exc)}).", capture)

        def after_click() -> ConfirmResult:
            deadline = datetime.now() + timedelta(seconds=CONFIRM_RESULT_TIMEOUT_S)
            while datetime.now() < deadline:
                if sel.PORTAL_HOST not in self.page.url:
                    # Una pasarela de pago externa: no se toca nada
                    capture = self._capture("pasarela")
                    return ConfirmResult("pasarela", "El portal ha abierto una pasarela de pago externa.", capture)
                if sel.CART_PATH not in self.page.url:
                    break
                alerts = self._visible_alerts()
                if alerts:
                    capture = self._capture("aviso")
                    return ConfirmResult("fallido", re.sub(r"\s+", " ", alerts[0])[:200], capture)
                self.page.wait_for_timeout(200)
            self.page.wait_for_load_state("networkidle")
            capture = self._capture("resultado")
            return classify_confirmation(self.page.url, self.page.locator("body").inner_text(), capture)

        return self._guard(run)

    def _capture(self, name: str) -> Path | None:
        """Guarda la página del resultado (datos personales: solo en data/, nunca al repo)."""
        if self.capture_dir is None:
            return None
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = self.capture_dir / f"{stamp}_{name}"
        try:
            base.with_suffix(".html").write_text(self.page.content(), encoding="utf-8")
            self.page.screenshot(path=base.with_suffix(".png"), full_page=True)
        except PlaywrightError:
            return None
        return base.with_suffix(".html")


# Verificado el 2026-09-30 con una compra real: tras «Confirmar la compra» se llega a
# /DeportesWeb/Modulos/VentaServicios/CarritoResultado con el resumen, «Monedero Pago 4,00 €»,
# «Operación <número>» y «Añadir a mi calendario». No hay un texto tipo «compra realizada».
OPERATION = re.compile(r"Operaci[oó]n\s+(\d{6,})")
FAILURE_WORDS = re.compile(r"no se ha podido|rechazad|saldo insuficiente|no hay plazas|agotad", re.I)


def classify_confirmation(url: str, text: str, capture: Path | None) -> ConfirmResult:
    text = re.sub(r"\s+", " ", text or "")
    if FAILURE_WORDS.search(text):
        return ConfirmResult("fallido", "El portal indicó un error al confirmar.", capture)
    operation = OPERATION.search(text)
    if "CarritoResultado" in url and operation:
        return ConfirmResult("reservado", f"Compra confirmada por el portal (operación {operation.group(1)}).", capture)
    return ConfirmResult("revisar", "No he podido comprobar el resultado de la compra.", capture)
