"""Ejecutar una reserva: preparar 1 min antes, pulsar a la hora exacta con reintentos, pagar con monedero o abono.

Cronología de un turno que abre a las T (T = inicio − 49 h):
    T − 60 s   el programador lanza run_booking: sesión (o login) y página del centro y día abierta
               si el carrito ya tiene algo, se abre: si es este turno se paga; si no, aviso por Telegram
    T          pulsa el turno; si el portal no lo deja aún o no responde, refresca y reintenta
    T + 10 min se deja de intentar (o antes si el portal lo muestra completo)
    aviso      si el portal habla del carrito (caducado, ya está en él, límite diario…) o su contador
               sube, se abre el carrito: si es este turno se sigue con él; si estaba caducado, el
               portal lo descarta al abrirlo y se vuelve a pulsar
    carrito    un solo elemento, que sea este turno, y monedero con saldo ≥ total o ninguna forma de
               pago (lo cubre el abono) → «Confirmar la compra»

Una reserva a la vez por perfil (el carrito y la sesión del portal son de la persona).

Siempre termina con un aviso por Telegram. Si no se consigue por falta de plazas, el aviso ofrece
«Observar» para vigilar el turno por si se libera.
"""

import logging
import sqlite3
import threading
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
    return f"{service} · {job.center}{lane} · {short_day(start)} {start:%H:%M}"


def short_day(moment: datetime) -> str:
    """«sáb 4 oct»."""
    return f"{WEEKDAYS[moment.weekday()]} {moment.day} {MONTHS[moment.month - 1]}"


def planned_message(settings: Settings, job: jobs_repo.Job) -> str:
    """Lo que se ve en la app mientras una reserva espera a que abra el turno."""
    opens = opening_of(settings, job)
    return f"Se reservará en cuanto abra: {short_day(opens)} a las {opens:%H:%M}."


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


# Una reserva a la vez por perfil: el carrito y la sesión del portal son de la persona, y dos
# navegadores con la misma sesión se pisan (02/10: el segundo dejaba al primero en «Error al
# procesar la petición» y el carrito con dos turnos). Perfiles distintos sí van en paralelo.
_PROFILE_LOCKS: dict[int, threading.Lock] = {}
_PROFILE_LOCKS_GUARD = threading.Lock()


def profile_lock(profile_id: int) -> threading.Lock:
    """Cerrojo del perfil: lo toma cada reserva, y anular lo necesita libre (misma sesión del portal)."""
    with _PROFILE_LOCKS_GUARD:
        return _PROFILE_LOCKS.setdefault(profile_id, threading.Lock())


def run_booking(settings: Settings, job_id: int, clock: Clock | None = None) -> str:
    """Ejecuta la reserva del job de principio a fin. Devuelve el estado final. Nunca lanza."""
    clock = clock or Clock(now=lambda: datetime.now(settings.zone))
    conn = connect(settings.data_dir)
    try:
        job = jobs_repo.get_job(conn, job_id)
        if job is None:
            return "desconocido"
        with profile_lock(job.profile_id):
            return _run(conn, settings, job_id, clock)
    except Exception as exc:  # hilo en segundo plano: cualquier fallo queda registrado y avisado
        log.exception("Reserva %s: error inesperado", job_id)
        return _finish(conn, settings, job_id, "fallido", f"Error inesperado del bot ({type(exc).__name__}).", offer_watch=False)
    finally:
        conn.close()


def _run(conn: sqlite3.Connection, settings: Settings, job_id: int, clock: Clock) -> str:
    job = jobs_repo.get_job(conn, job_id)
    if job is not None and job.dry_run and job.is_active:
        # Ya no hay modo prueba (02/10): una prueba antigua que siguiera programada no se vuelve real
        jobs_repo.set_status(conn, job_id, "cancelado", "Era una prueba: el modo prueba ya no existe.", only_if=STARTABLE)
        conn.commit()
        return "cancelado"
    if job is None or not jobs_repo.set_status(conn, job_id, "reservando", only_if=STARTABLE):
        return job.status if job else "desconocido"
    conn.commit()

    profile = profiles_repo.get_profile(conn, job.profile_id)
    zone = settings.zone
    start = job.slot_at.astimezone(zone)
    opens = opening_of(settings, job)
    day, at = start.date(), start.time()
    capture_dir = settings.data_dir / "capturas" / "reservas" / f"job{job.id}"

    password = crypto.decrypt(settings, profiles_repo.get_portal_password_enc(conn, profile.id))
    try:
        with BookingSession(settings, profile.id, capture_dir) as session:
            _prepare_with_retries(conn, job, session, profile.portal_username, password, day, opens, clock)
            del password
            offset = session.server_clock_offset()
            clock_note = f"; reloj del portal {offset:+.1f} s" if offset is not None else ""
            _attempt(conn, job.id, "preparar", "ok", f"página lista a las {clock.now():%H:%M:%S}{clock_note}")

            # Un carrito con algo pendiente (aunque esté caducado) bloquea añadir el turno: se mira ya,
            # antes de la hora, para no descubrirlo en el momento de pulsar (30/09 18:30)
            in_cart = False
            pending = session.cart_count()
            if pending:
                _attempt(conn, job.id, "carrito previo", "aviso", f"el carrito tiene {pending} elemento(s) antes de la apertura")
                in_cart = _check_cart(conn, settings, job, session, day, at, warn=True) == "este_turno"
                if not in_cart:
                    _back_to_slot(conn, job, session, day)

            # Pulsar cuando en el reloj del PORTAL ya sean las T (medido ±0,5 s: se toma el mínimo,
            # así nunca se pulsa antes de tiempo; observado +0,9 s el 2026-09-30)
            target = opens - portal_clock_lead(offset)
            if not in_cart and clock.now() < target:
                clock.wait_until(target)

            if not in_cart:
                in_cart, reason = _click_until_in_cart(conn, settings, job, session, at, day, clock)
            if not in_cart:
                session.capture("sin_conseguir")
                return _finish(conn, settings, job.id, "fallido", reason, offer_watch=True)

            cart = session.read_cart(day, at)
            problem = _cart_problem(cart)
            if problem:
                session.capture("carrito_no_valido")  # para estudiar carritos nuevos (p. ej. con abono)
                _attempt(conn, job.id, "carrito", "no", problem)
                return _finish(conn, settings, job.id, "fallido", f"{problem} {LEFT_IN_CART}", offer_watch=False)

            # Última comprobación antes de comprar: que nadie lo haya cancelado mientras tanto
            current = jobs_repo.get_job(conn, job.id)
            if current is None or current.status != "reservando":
                _attempt(conn, job.id, "confirmar", "no", "cancelado antes de confirmar")
                return current.status if current else "cancelado"

            result = session.confirm_purchase(cart)
            _attempt(conn, job.id, "confirmar", result.status, result.message)
            if result.status == "reservado":
                if cart.covered_by_pass:
                    return _finish(conn, settings, job.id, "reservado", "Con tu bono mensual.", ["🎫 Con tu bono mensual"])
                balance = cart.wallet_balance - cart.total
                return _finish(conn, settings, job.id, "reservado",
                               f"Pagada con el monedero ({euros(cart.total)}). Saldo: {euros(balance)}.",
                               [f"💳 Pagada con el monedero: {euros(cart.total)}", f"💰 Saldo del monedero: {euros(balance)}"])
            if result.status == "pasarela":
                return _finish(conn, settings, job.id, "fallido",
                               "El portal pidió pagar con tarjeta: no he seguido. Revisa el monedero.", offer_watch=False)
            if result.status == "revisar":
                return _finish(conn, settings, job.id, "revisar",
                               "He confirmado la compra pero no he podido comprobar el resultado. Revísalo en Madrid Móvil.")
            return _finish(conn, settings, job.id, "fallido", result.message, offer_watch=True)
    except portal.BrowserClosed as exc:
        _attempt(conn, job.id, "navegador", "cerrado", str(exc))
        return _finish(conn, settings, job.id, "fallido", str(exc), offer_watch=True)
    except portal.PortalError as exc:
        _attempt(conn, job.id, "portal", "error", str(exc))
        return _finish(conn, settings, job.id, "fallido", str(exc), offer_watch=True)


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


def _back_to_slot(conn, job, session, day) -> None:
    """Tras mirar el carrito antes de la hora, vuelve a la página del turno (con reintentos)."""
    for n in range(1, PREPARE_TRIES + 1):
        try:
            session.recover(job.service, job.center_id, day)
            return
        except portal.BrowserClosed:
            raise
        except portal.PortalError as exc:
            _attempt(conn, job.id, f"volver al turno #{n}", "error", str(exc))
            if n == PREPARE_TRIES:
                raise


def _check_cart(conn, settings, job, session, day, at, warn: bool) -> str:
    """Abre el carrito y mira qué hay. Devuelve:
    "este_turno"  es justo este turno (listo para pagar con las comprobaciones de siempre)
    "caducado"    había caducado y el portal lo ha descartado al abrirlo: ya no bloquea
    "otra_cosa"   otra cosa (o no se pudo abrir): se avisa por Telegram si warn y se sigue;
                  el bot nunca vacía el carrito"""
    try:
        opened = session.open_cart()
        cart = session.read_cart(day, at) if opened == "listo" else None
    except portal.BrowserClosed:
        raise
    except portal.PortalError as exc:
        _attempt(conn, job.id, "carrito", "error", str(exc))
        return "otra_cosa"
    if opened == "expirado":
        # Abrirlo hace que el portal lo descarte (02/10): ya no bloquea, no hace falta avisar
        _attempt(conn, job.id, "carrito", "caducado", "el carrito había caducado y el portal lo ha descartado")
        return "caducado"
    if opened != "listo":
        _attempt(conn, job.id, "carrito", "no", "no se pudo abrir el carrito o no se puede pagar")
        if warn:
            _warn_cart(conn, settings, job, "Tu carrito del portal tiene algo pendiente que no he podido abrir "
                                            "y puede bloquear la reserva.")
        return "otra_cosa"
    if cart.items == 1 and cart.matches_slot:
        _attempt(conn, job.id, "carrito", "retomado", "el turno está en el carrito")
        return "este_turno"
    _attempt(conn, job.id, "carrito", "no", f"el carrito tiene {cart.items} elemento(s) que no son este turno")
    if warn:
        _warn_cart(conn, settings, job, f"Tu carrito del portal tiene {cart.items} elemento(s) que no son este "
                                        "turno y pueden bloquear la reserva.")
    return "otra_cosa"


def _warn_cart(conn, settings, job, text: str) -> None:
    notify(conn, settings, job.profile_id,
           f"⚠️ Carrito pendiente\n{slot_label(settings, job)}\n{text} Sigo intentándolo; "
           "no lo vacío yo. Si puedes, revísalo en Madrid Móvil.")


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
    warned = False
    for n in range(1, MAX_CLICKS + 1):
        try:
            outcome = session.try_add_to_cart(at, job.activity)
        except portal.BrowserClosed as exc:
            _attempt(conn, job.id, f"pulsar #{n}", "no", str(exc))
            return False, str(exc)
        except portal.PortalError as exc:
            outcome = AddOutcome(False, str(exc), page_broken=True)
        _attempt(conn, job.id, f"pulsar #{n}", "carrito" if outcome.in_cart else "no", outcome.message)
        if outcome.in_cart:
            return True, ""
        already = ALREADY_BOOKED in outcome.message
        if already or outcome.check_cart:
            # El portal habla del carrito (turno ya en él, carrito caducado, el contador subió sin que la
            # página fuera al carrito…): se abre y, si es este turno, se sigue con él hasta pagarlo
            found = _check_cart(conn, settings, job, session, day, at, warn=not warned)
            if found == "este_turno":
                return True, ""
            warned = warned or found == "otra_cosa"
            if already and found == "otra_cosa":
                return False, "Ya tienes este turno reservado (no está en el carrito para pagarlo)."
            # Se vuelve a la página del turno y se sigue (con espera creciente si algo sigue bloqueando)
            outcome = AddOutcome(False, outcome.message, page_broken=True)
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
        except portal.BrowserClosed as exc:
            _attempt(conn, job.id, "reentrar", "error", str(exc))
            return False, str(exc)
        except portal.PortalError as exc:
            _attempt(conn, job.id, "reentrar", "error", str(exc))
    return False, f"{reason} (sin éxito tras {MAX_CLICKS} intentos)"


def _minutes(delta: timedelta) -> str:
    minutes = delta.total_seconds() / 60
    return f"{minutes:.0f} min" if minutes >= 1 else f"{delta.total_seconds():.0f} s"


LEFT_IN_CART = ("El turno se ha quedado en tu carrito del portal: si no lo quieres, elimínalo en Madrid Móvil, "
                "porque mientras siga ahí bloquea otras reservas (el bot nunca vacía el carrito).")


def _cart_problem(cart) -> str | None:
    if cart.items != 1 or not cart.matches_slot:
        return (f"El carrito tiene {cart.items} elementos y no solo este turno: no he confirmado para no pagar "
                "otras cosas.")
    if cart.needs_terms:
        return "El portal pide aceptar condiciones antes de pagar: no he confirmado."
    if cart.covered_by_pass:
        return None  # sin formas de pago: el turno entra en el abono y se confirma sin pagar nada
    if cart.wallet_balance is None:
        return "El monedero no aparece como forma de pago: no he confirmado (el bot nunca usa tarjeta ni Bizum)."
    if cart.total is None:
        return "No he podido leer el total del carrito: no he confirmado."
    if cart.wallet_balance < cart.total:
        return (f"Sin saldo suficiente en el monedero ({euros(cart.wallet_balance)} para {euros(cart.total)}): "
                "no he confirmado.")
    return None


def euros(amount) -> str:
    return f"{amount:.2f} €".replace(".", ",") if amount is not None else "?"


def _attempt(conn, job_id, action, result, message="") -> None:
    jobs_repo.add_attempt(conn, job_id, action, result, message)
    conn.commit()


TITLES = {
    "reservado": "✅ Reserva confirmada",
    "revisar": "⚠️ Reserva por comprobar",
    "fallido": "❌ No se pudo reservar",
}


def _finish(conn, settings, job_id: int, status: str, message: str, lines: list[str] | None = None,
            offer_watch: bool = False) -> str:
    """Guarda el estado final y avisa por Telegram. message es lo que se ve en la app; lines, si las
    hay, lo sustituyen en Telegram (una por línea)."""
    jobs_repo.set_status(conn, job_id, status, message)
    conn.commit()
    job = jobs_repo.get_job(conn, job_id)
    buttons = None
    if offer_watch and job.mode == "reservar" and job.slot_at > datetime.now(job.slot_at.tzinfo):
        buttons = [("👀 Observar", f"obs:{job.id}"), ("Ignorar", f"ign:{job.id}")]
    body = "\n".join(lines) if lines else message
    notify(conn, settings, job.profile_id, f"{TITLES[status]}\n{slot_label(settings, job)}\n{body}", buttons)
    return status
