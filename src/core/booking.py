"""Ejecutar una reserva: preparar 1 min antes, pulsar a la hora exacta con reintentos, pagar con monedero.

Cronología de un turno que abre a las T (T = inicio − 49 h):
    T − 60 s   el programador lanza run_booking: sesión (o login) y página del centro y día abierta
    T          pulsa el turno; si el portal no lo deja aún o no responde, refresca y reintenta
    T + 25 s   se deja de intentar (o antes si el portal lo muestra completo)
    carrito    un solo elemento, que sea este turno, monedero con saldo ≥ total → «Confirmar la compra»

Modo prueba: todo igual hasta T, pero no pulsa el turno (pulsarlo ya lo mete en el carrito);
solo comprueba que a esa hora se podía pulsar.

Siempre termina con un aviso por Telegram. Si no se consigue por falta de plazas, el aviso ofrece
«Observar» para vigilar el turno por si se libera.
"""

import logging
import sqlite3
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from src import portal
from src.config import Settings
from src.core import slots as core_slots
from src.db import crypto
from src.db import jobs as jobs_repo
from src.db import profiles as profiles_repo
from src.db.database import connect
from src.notify import notify
from src.portal.booking import AddOutcome, BookingSession
from src.portal.selectors import ALREADY_BOOKED_TEXT as ALREADY_BOOKED

log = logging.getLogger(__name__)

PREPARE_BEFORE = timedelta(seconds=60)
# Con el portal saturado en la apertura, un clic puede tardar decenas de segundos en responder
# La ventana de intentos tras la apertura es settings.booking_window_s (10 min por defecto): entre
# semana el portal se cae varios minutos en la apertura y hay que seguir cuando vuelve.
MAX_CLICKS = 300  # solo un seguro; lo que manda es la ventana
# Si el portal no responde o la página se rompe, se espera cada vez más antes de volver a entrar,
# para no machacarlo mientras está caído (en cuanto responde se vuelve a ir rápido)
BACKOFF_STEPS_S = (2, 4, 8, 15)
# Cada cuántos clics fallidos se recarga la lista para ver si ya está completo (recargar es lento)
REFRESH_EVERY = 3
# Intentos de preparar la página (login + centro + día) antes de la apertura, si el portal va lento
PREPARE_TRIES = 3
# Las demás tareas del navegador (precarga, vigilancias) no corren en este margen alrededor de T
QUIET_MARGIN = timedelta(minutes=2)

WEEKDAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

# Estados desde los que se puede lanzar una reserva
STARTABLE = ("pendiente", "esperando_apertura", "plaza_liberada", "vigilando", "avisado")


@dataclass
class Clock:
    """Reloj y espera; en los tests se sustituyen para no esperar de verdad."""

    now: Callable[[], datetime]
    sleep: Callable[[float], None] = time_module.sleep

    def wait_until(self, target: datetime) -> None:
        """Duerme hasta target con precisión de milisegundos (el reloj del sistema debe ir por NTP)."""
        while True:
            remaining = (target - self.now()).total_seconds()
            if remaining <= 0:
                return
            self.sleep(remaining - 0.3 if remaining > 0.5 else min(remaining, 0.005))


def slot_label(settings: Settings, job: jobs_repo.Job) -> str:
    start = job.slot_at.astimezone(settings.zone)
    lane = f" · {job.activity.split(' · ', 1)[1]}" if " · " in job.activity else ""
    service = jobs_repo.SERVICES[job.service]
    return f"{service} · {job.center}{lane} · {WEEKDAYS[start.weekday()]} {start.day} {MONTHS[start.month - 1]} {start:%H:%M}"


def opening_of(settings: Settings, job: jobs_repo.Job) -> datetime:
    return core_slots.opening_time(job.slot_at.astimezone(settings.zone))


MAX_CLOCK_LEAD = timedelta(seconds=5)


def portal_clock_lead(offset_s: float | None) -> timedelta:
    """Cuánto antes (en nuestro reloj) es seguro pulsar: lo mínimo que va adelantado el del portal."""
    if offset_s is None:
        return timedelta(0)
    return min(max(timedelta(seconds=offset_s - 0.5), timedelta(0)), MAX_CLOCK_LEAD)


def booking_imminent(conn: sqlite3.Connection, settings: Settings, now: datetime) -> bool:
    """¿Hay una apertura a punto de llegar o en curso? Entonces el navegador es solo para reservar."""
    for job in jobs_repo.list_active(conn):
        if job.status == "reservando":
            return True
        if job.mode != "reservar":
            continue
        opens = opening_of(settings, job)
        if opens - PREPARE_BEFORE - QUIET_MARGIN <= now <= opens + QUIET_MARGIN:
            return True
    return False


def run_booking(settings: Settings, job_id: int, clock: Clock | None = None) -> str:
    """Ejecuta la reserva del job de principio a fin. Devuelve el estado final. Nunca lanza."""
    clock = clock or Clock(now=lambda: datetime.now(settings.zone))
    conn = connect(settings.data_dir)
    try:
        return _run(conn, settings, job_id, clock)
    except Exception as exc:  # hilo en segundo plano: cualquier fallo queda registrado y avisado
        log.exception("Reserva %s: error inesperado", job_id)
        return _finish(conn, settings, job_id, "fallido", f"Error inesperado del bot ({type(exc).__name__}).", offer_watch=False)
    finally:
        conn.close()


def _run(conn: sqlite3.Connection, settings: Settings, job_id: int, clock: Clock) -> str:
    job = jobs_repo.get_job(conn, job_id)
    if job is None or not jobs_repo.set_status(conn, job_id, "reservando", only_if=STARTABLE):
        return job.status if job else "desconocido"
    conn.commit()

    profile = profiles_repo.get_profile(conn, job.profile_id)
    zone = settings.zone
    start = job.slot_at.astimezone(zone)
    opens = opening_of(settings, job)
    day, at = start.date(), start.time()
    dry_run = job.dry_run or settings.dry_run
    capture_dir = settings.data_dir / "capturas" / "reservas" / f"job{job.id}"

    password = crypto.decrypt(settings, profiles_repo.get_portal_password_enc(conn, profile.id))
    try:
        with BookingSession(settings, profile.id, capture_dir) as session:
            _prepare_with_retries(conn, job, session, profile.portal_username, password, day, opens, clock)
            del password
            offset = session.server_clock_offset()
            clock_note = f"; reloj del portal {offset:+.1f} s" if offset is not None else ""
            _attempt(conn, job.id, "preparar", "ok", f"página lista a las {clock.now():%H:%M:%S}{clock_note}")

            # Pulsar cuando en el reloj del PORTAL ya sean las T (medido ±0,5 s: se toma el mínimo,
            # así nunca se pulsa antes de tiempo; observado +0,9 s el 2026-09-30)
            target = opens - portal_clock_lead(offset)
            if clock.now() < target:
                clock.wait_until(target)

            if dry_run:
                return _dry_run(conn, settings, job, session, at, clock)

            in_cart, reason = _click_until_in_cart(conn, settings, job, session, at, day, clock)
            if not in_cart:
                session.capture("sin_conseguir")
                return _finish(conn, settings, job.id, "fallido", reason, offer_watch=True)

            cart = session.read_cart(day, at)
            problem = _cart_problem(cart)
            if problem:
                _attempt(conn, job.id, "carrito", "no", problem)
                return _finish(conn, settings, job.id, "fallido", problem, offer_watch=False)

            # Última comprobación antes de comprar: que nadie lo haya cancelado mientras tanto
            current = jobs_repo.get_job(conn, job.id)
            if current is None or current.status != "reservando":
                _attempt(conn, job.id, "confirmar", "no", "cancelado antes de confirmar")
                return current.status if current else "cancelado"

            result = session.confirm_with_wallet(cart)
            _attempt(conn, job.id, "confirmar", result.status, result.message)
            if result.status == "reservado":
                operation = result.message.partition("(operación ")[2].rstrip(").")
                note = f", operación {operation}" if operation else ""
                return _finish(conn, settings, job.id, "reservado", f"Pagado con el monedero ({_euros(cart.total)}{note}).")
            if result.status == "pasarela":
                return _finish(conn, settings, job.id, "fallido",
                               "El portal pidió pagar con tarjeta: no he seguido. Revisa el monedero.", offer_watch=False)
            if result.status == "revisar":
                return _finish(conn, settings, job.id, "revisar",
                               "He confirmado la compra pero no he podido comprobar el resultado. Revísalo en Madrid Móvil.")
            return _finish(conn, settings, job.id, "fallido", result.message, offer_watch=True)
    except portal.PortalError as exc:
        _attempt(conn, job.id, "portal", "error", str(exc))
        return _finish(conn, settings, job.id, "fallido", str(exc), offer_watch=True)


def _dry_run(conn, settings, job, session, at, clock) -> str:
    session.refresh_day()
    state = session.slot_state(at, job.activity)
    moment = clock.now().strftime("%H:%M:%S")
    if state.found and state.selectable:
        _attempt(conn, job.id, "prueba", "ok", f"se podía pulsar a las {moment} ({state.free}/{state.total} libres)")
        return _finish(conn, settings, job.id, "prueba_ok",
                       f"Prueba: a las {moment} el turno se podía pulsar ({state.free}/{state.total} libres). No se ha reservado.")
    _attempt(conn, job.id, "prueba", "no", f"a las {moment} el turno no se podía pulsar")
    return _finish(conn, settings, job.id, "fallido",
                   f"Prueba: a las {moment} el turno no se podía pulsar (completo o no aparece).", offer_watch=True)


def _prepare_with_retries(conn, job, session, username, password, day, opens, clock) -> None:
    """Sesión + centro + día. Si el portal va lento, se reintenta mientras quede margen antes de T."""
    for n in range(1, PREPARE_TRIES + 1):
        try:
            relogged = session.start(username, password)
            _attempt(conn, job.id, "sesion", "ok", "login hecho" if relogged else "sesión guardada válida")
            session.prepare(job.service, job.center_id, day)
            return
        except portal.PortalError as exc:
            _attempt(conn, job.id, f"preparar #{n}", "error", str(exc))
            if n == PREPARE_TRIES or (clock.now() > opens and n >= 2):
                raise


def attempt_window(settings: Settings) -> timedelta:
    return timedelta(seconds=settings.booking_window_s)


def _click_until_in_cart(conn, settings, job, session, at, day, clock) -> tuple[bool, str]:
    """Pulsa el turno hasta que entra en el carrito, el portal lo da por completo o se acaba la ventana.

    Un error del portal (no responde, la lista desaparece, se cae…) no acaba la reserva: se espera
    un poco más cada vez, se vuelve a entrar en la página y se sigue mientras quede ventana.
    """
    deadline = clock.now() + attempt_window(settings)
    reason = "No se pudo reservar."
    failures_in_a_row = 0
    for n in range(1, MAX_CLICKS + 1):
        try:
            outcome = session.try_add_to_cart(at, job.activity)
        except portal.PortalError as exc:
            outcome = AddOutcome(False, str(exc), page_broken=True)
        _attempt(conn, job.id, f"pulsar #{n}", "carrito" if outcome.in_cart else "no", outcome.message)
        if outcome.in_cart:
            return True, ""
        if ALREADY_BOOKED in outcome.message:
            # Ya lo tienes: o está en tu carrito sin pagar (p. ej. de un intento anterior) o ya reservado.
            # Se abre el carrito; quien llama comprueba que solo está este turno antes de pagar.
            if session.open_cart():
                _attempt(conn, job.id, "carrito", "retomado", "el turno ya estaba en el carrito")
                return True, ""
            return False, "Ya tienes este turno reservado (o en el carrito) y no pude abrir el carrito."
        reason = outcome.message or reason
        if clock.now() >= deadline:
            return False, f"{reason} (sin éxito tras {n} intentos en {_minutes(attempt_window(settings))})"
        try:
            if outcome.page_broken:
                failures_in_a_row += 1
                wait = BACKOFF_STEPS_S[min(failures_in_a_row, len(BACKOFF_STEPS_S)) - 1]
                if clock.now() + timedelta(seconds=wait) >= deadline:
                    return False, f"{reason} (el portal no respondió en {_minutes(attempt_window(settings))})"
                session.capture(f"intento{n}")
                clock.sleep(wait)
                session.recover(job.service, job.center_id, day)
                _attempt(conn, job.id, "reentrar", "ok", f"página del turno abierta de nuevo (tras esperar {wait} s)")
            else:
                failures_in_a_row = 0  # el portal ha respondido al clic: se vuelve a ir rápido
                # Volver a pulsar directamente es lo más rápido; solo de vez en cuando se recarga la
                # lista (lento con el portal saturado) para saber si ya está completo
                if n % REFRESH_EVERY != 0:
                    continue
                session.refresh_day()
            state = session.slot_state(at, job.activity)
            if state.found and state.free == 0:
                return False, "Completo: no quedan plazas."
        except portal.PortalError as exc:
            _attempt(conn, job.id, "reentrar", "error", str(exc))
    return False, f"{reason} (sin éxito tras {MAX_CLICKS} intentos)"


def _minutes(delta: timedelta) -> str:
    minutes = delta.total_seconds() / 60
    return f"{minutes:.0f} min" if minutes >= 1 else f"{delta.total_seconds():.0f} s"


def _cart_problem(cart) -> str | None:
    if cart.items != 1 or not cart.matches_slot:
        return (f"El carrito tiene {cart.items} elementos y no solo este turno: no he confirmado para no pagar "
                "otras cosas. Revisa el carrito en Madrid Móvil (caduca solo).")
    if cart.needs_terms:
        return "El portal pide aceptar condiciones antes de pagar: no he confirmado."
    if cart.wallet_balance is None:
        return "El monedero no aparece como forma de pago: no he confirmado (el bot nunca usa tarjeta ni Bizum)."
    if cart.total is None:
        return "No he podido leer el total del carrito: no he confirmado."
    if cart.wallet_balance < cart.total:
        return (f"Sin saldo suficiente en el monedero ({_euros(cart.wallet_balance)} para {_euros(cart.total)}): "
                "no he confirmado. El turno queda en tu carrito del portal hasta que caduque.")
    return None


def _euros(amount) -> str:
    return f"{amount:.2f} €".replace(".", ",") if amount is not None else "?"


def _attempt(conn, job_id, action, result, message="") -> None:
    jobs_repo.add_attempt(conn, job_id, action, result, message)
    conn.commit()


ICONS = {"reservado": "✅", "prueba_ok": "🧪", "revisar": "⚠️", "fallido": "❌"}


def _finish(conn, settings, job_id: int, status: str, message: str, offer_watch: bool = False) -> str:
    jobs_repo.set_status(conn, job_id, status, message)
    conn.commit()
    job = jobs_repo.get_job(conn, job_id)
    title = {
        "reservado": "Reserva conseguida",
        "prueba_ok": "Prueba superada",
        "revisar": "Reserva por comprobar",
        "fallido": "No se pudo reservar",
    }[status]
    buttons = None
    if offer_watch and job.mode == "reservar" and job.slot_at > datetime.now(job.slot_at.tzinfo):
        buttons = [("👀 Observar", f"obs:{job.id}"), ("Ignorar", f"ign:{job.id}")]
    notify(conn, settings, job.profile_id, f"{ICONS[status]} {title}\n{slot_label(settings, job)}\n{message}", buttons)
    return status
