"""La reserva de principio a fin con un portal falso y un reloj falso (nunca el portal real)."""

from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright

from src.config import Settings
from src.core import booking as core_booking
from src.core import telegram_actions, watch
from src.db import centers as centers_repo
from src.db import crypto
from src.db import jobs as jobs_repo
from src.db import profiles as profiles_repo
from src.db import slots as slots_repo
from src.db.database import connect, init_db
from src.notify.telegram import CallbackQuery
from src.portal.booking import AddOutcome, BookingSession, Cart, ConfirmResult, SlotState, classify_confirmation, parse_money, spanish_date
from src.worker.scheduler import Worker

MADRID = ZoneInfo("Europe/Madrid")
SLOT = datetime(2026, 10, 3, 19, 0, tzinfo=MADRID)
OPENS = SLOT - timedelta(hours=49)  # 1 oct 18:00
FIXTURES = Path(__file__).parent / "fixtures"


# --- dobles de prueba ---------------------------------------------------------

class FakeClock:
    def __init__(self, start):
        self.t = start
        self.slept = 0.0

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept += seconds
        self.t += timedelta(seconds=seconds)


class FakeSession:
    """Imita BookingSession. clicks: lista de AddOutcome que se devuelven en orden."""

    instances: list["FakeSession"] = []

    def __init__(self, clock, clicks=None, cart=None, confirm=None, state=None):
        self.clock = clock
        self.clicks = list(clicks) if clicks is not None else [AddOutcome(True, "")]
        self.cart = cart or Cart(1, True, Decimal("4.00"), Decimal("20.00"), False)
        self.confirm = confirm or ConfirmResult("reservado", "Compra confirmada por el portal.", None)
        self.state = state or SlotState(True, True, 3, 10)
        self.click_times: list[datetime] = []
        self.confirmed = False
        self.prepared_at = None

    def __call__(self, *_args, **_kwargs):  # BookingSession(settings, profile_id, dir)
        FakeSession.instances.append(self)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def start(self, username, password):
        assert password == "clave-portal"
        return False

    def prepare(self, service, center_id, day):
        self.prepared_at = self.clock.now()

    def server_clock_offset(self):
        return 0.3

    def recover(self, service, center_id, day):
        self.recoveries = getattr(self, "recoveries", 0) + 1
        self.clock.t += timedelta(seconds=5)

    def capture(self, name):
        return None

    def refresh_day(self):
        self.clock.t += timedelta(seconds=1)

    def slot_state(self, at, activity):
        return self.state

    def try_add_to_cart(self, at, activity):
        self.click_times.append(self.clock.now())
        self.clock.t += timedelta(milliseconds=300)
        return self.clicks.pop(0) if self.clicks else AddOutcome(False, "El portal no respondió al pulsar el turno.")

    def read_cart(self, day, at):
        return self.cart

    pending = 0  # contador del carrito antes de empezar

    def cart_count(self):
        return self.pending

    cart_page = "listo"  # lo que encuentra al abrir el carrito: listo | expirado | no

    def open_cart(self):
        self.opened_cart = True
        return self.cart_page

    def confirm_purchase(self, cart):
        self.confirmed = True
        return self.confirm


@pytest.fixture
def settings(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path, fernet_key=Fernet.generate_key().decode(), dry_run=False)
    init_db(s.data_dir)
    conn = connect(s.data_dir)
    profiles_repo.create_profile(conn, "Alicia", "#0f766e", "a@example.com", crypto.encrypt(s, "clave-portal"))
    profiles_repo.set_telegram_chat(conn, 1, 111)
    centers_repo.replace_service_centers(conn, "piscina", [(58, "Daoíz y Velarde", "")])
    conn.commit()
    conn.close()
    return s


@pytest.fixture
def sent(monkeypatch):
    messages = []
    monkeypatch.setattr(core_booking, "notify", lambda conn, s, pid, text, buttons=None: messages.append((text, buttons)) or True)
    monkeypatch.setattr(watch, "notify", lambda conn, s, pid, text, buttons=None: messages.append((text, buttons)) or True)
    return messages


def make_job(settings, mode="reservar", dry_run=False, on_free=None, status=None):
    conn = connect(settings.data_dir)
    job_id = jobs_repo.create_job(conn, 1, 58, "Daoíz y Velarde", "Nado libre · Calle central", "piscina",
                                  SLOT, mode, on_free or "reservar", dry_run)
    if status:
        jobs_repo.set_status(conn, job_id, status)
    conn.commit()
    conn.close()
    return job_id


def run(settings, job_id, session, clock):
    import src.core.booking as mod

    original = mod.BookingSession
    mod.BookingSession = session
    try:
        return core_booking.run_booking(settings, job_id, core_booking.Clock(now=clock.now, sleep=clock.sleep))
    finally:
        mod.BookingSession = original


def job_of(settings, job_id):
    conn = connect(settings.data_dir)
    try:
        return jobs_repo.get_job(conn, job_id), jobs_repo.list_attempts(conn, job_id)
    finally:
        conn.close()


# --- la reserva ---------------------------------------------------------------

def test_prepares_early_and_clicks_exactly_at_opening(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    session = FakeSession(clock)
    assert run(settings, make_job(settings), session, clock) == "reservado"

    assert session.prepared_at == OPENS - timedelta(seconds=60)
    assert session.click_times[0] == OPENS  # ni antes ni después
    assert session.confirmed
    job, attempts = job_of(settings, 1)
    assert job.status == "reservado" and "4,00 €" in job.result_message
    assert [a.action for a in attempts] == ["sesion", "preparar", "pulsar #1", "confirmar"]
    assert sent[-1][0].startswith("✅ Reserva conseguida") and sent[-1][1] is None


def test_retries_until_in_cart(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    session = FakeSession(clock, clicks=[AddOutcome(False, "Aún no disponible"), AddOutcome(False, ""), AddOutcome(True, "")])
    assert run(settings, make_job(settings), session, clock) == "reservado"
    assert len(session.click_times) == 3
    assert session.click_times[-1] - session.click_times[0] < timedelta(seconds=5)


def test_full_slot_fails_and_offers_watch(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    session = FakeSession(clock, clicks=[AddOutcome(False, "Sin plazas")] * 3, state=SlotState(True, False, 0, 10))
    assert run(settings, make_job(settings), session, clock) == "fallido"
    assert not session.confirmed
    text, buttons = sent[-1]
    assert text.startswith("❌ No se pudo reservar") and "Completo" in text
    assert buttons == [("👀 Observar", "obs:1"), ("Ignorar", "ign:1")]


def test_gives_up_after_the_attempt_window(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    session = FakeSession(clock, clicks=[])  # el portal nunca responde
    assert run(settings, make_job(settings), session, clock) == "fallido"
    assert session.click_times[-1] - OPENS <= core_booking.attempt_window(settings) + timedelta(seconds=5)
    assert len(session.click_times) <= core_booking.MAX_CLICKS


def test_not_enough_wallet_balance_never_confirms(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    session = FakeSession(clock, cart=Cart(1, True, Decimal("4.00"), Decimal("1.50"), False))
    assert run(settings, make_job(settings), session, clock) == "fallido"
    assert not session.confirmed
    assert "Sin saldo suficiente" in sent[-1][0] and "1,50 €" in sent[-1][0]
    assert sent[-1][1] is None  # observar no arregla la falta de saldo


@pytest.mark.parametrize(
    ("cart", "text"),
    [
        (Cart(2, False, Decimal("8.00"), Decimal("20.00"), False), "El carrito tiene 2 elementos"),
        (Cart(1, True, Decimal("4.00"), None, False), "El monedero no aparece"),
        (Cart(1, True, Decimal("4.00"), Decimal("20.00"), True), "aceptar condiciones"),
    ],
)
def test_unsafe_cart_never_confirms(settings, sent, cart, text):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    session = FakeSession(clock, cart=cart)
    assert run(settings, make_job(settings), session, clock) == "fallido"
    assert not session.confirmed
    assert text in sent[-1][0]


def test_card_payment_gateway_is_an_error(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    session = FakeSession(clock, confirm=ConfirmResult("pasarela", "pasarela", None))
    assert run(settings, make_job(settings), session, clock) == "fallido"
    assert "pidió pagar con tarjeta" in sent[-1][0]


def test_unclear_result_asks_to_check(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    session = FakeSession(clock, confirm=ConfirmResult("revisar", "?", None))
    assert run(settings, make_job(settings), session, clock) == "revisar"
    assert "Revísalo en Madrid Móvil" in sent[-1][0]


def test_dry_run_never_clicks_the_slot(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    session = FakeSession(clock)
    assert run(settings, make_job(settings, dry_run=True), session, clock) == "prueba_ok"
    assert session.click_times == [] and not session.confirmed
    assert sent[-1][0].startswith("🧪 Prueba superada")


def test_server_dry_run_wins_over_job(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    session = FakeSession(clock)
    forced = settings.model_copy(update={"dry_run": True})
    assert run(forced, make_job(settings, dry_run=False), session, clock) == "prueba_ok"
    assert session.click_times == []


def test_cancelled_while_running_does_not_confirm(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    job_id = make_job(settings)

    class CancelInCart(FakeSession):
        def read_cart(self, day, at):
            conn = connect(settings.data_dir)
            conn.execute("UPDATE jobs SET status = 'cancelado' WHERE id = ?", (job_id,))
            conn.commit()
            conn.close()
            return self.cart

    session = CancelInCart(clock)
    assert run(settings, job_id, session, clock) == "cancelado"
    assert not session.confirmed


def test_already_finished_job_is_not_run_again(settings, sent):
    clock = FakeClock(OPENS)
    session = FakeSession(clock)
    job_id = make_job(settings, status="reservado")
    assert run(settings, job_id, session, clock) == "reservado"
    assert FakeSession.instances[-1] is not session or session.click_times == []


# --- botones de Telegram --------------------------------------------------------

class FakeTelegram:
    def __init__(self):
        self.answers, self.sent = [], []

    def answer_callback(self, cid, text):
        self.answers.append(text)

    def clear_buttons(self, chat, msg):
        pass

    def send_message(self, chat, text, buttons=None):
        self.sent.append(text)


def test_observe_button_creates_one_watch(settings, monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setattr(telegram_actions.linking, "client", lambda _s: fake)
    job_id = make_job(settings, status="fallido")
    conn = connect(settings.data_dir)
    cb = CallbackQuery(1, "cb", 111, 5, f"obs:{job_id}")
    telegram_actions.handle_callback(conn, settings, cb, start_booking=lambda _id: None)
    telegram_actions.handle_callback(conn, settings, cb, start_booking=lambda _id: None)  # doble toque

    watches = [j for j in jobs_repo.list_active(conn) if j.mode == "observar"]
    assert len(watches) == 1 and watches[0].on_free == "reservar" and watches[0].activity == "Nado libre · Calle central"
    assert fake.answers[0].startswith("👀 Observando")
    conn.close()


def test_button_from_another_chat_is_rejected(settings, monkeypatch):
    monkeypatch.setattr(telegram_actions.linking, "client", lambda _s: FakeTelegram())
    job_id = make_job(settings, status="fallido")
    conn = connect(settings.data_dir)
    reply = telegram_actions.handle_callback(conn, settings, CallbackQuery(1, "cb", 999, 5, f"obs:{job_id}"), lambda _id: None)
    assert reply == "Este botón no es de tu perfil."
    assert not [j for j in jobs_repo.list_active(conn) if j.mode == "observar"]
    conn.close()


# --- vigilancia -----------------------------------------------------------------

def seed_slot(settings, free, now):
    conn = connect(settings.data_dir)
    slots_repo.replace_days(conn, "piscina", 58, [SLOT.date()],
                            [slots_repo.StoredSlot(SLOT.date(), time(19), "Nado libre · Calle central", free, 10, free > 0)], now)
    conn.execute("UPDATE profiles SET portal_status = 'ok'")
    conn.commit()
    conn.close()


@pytest.mark.parametrize(("on_free", "expected_status"), [("reservar", "plaza_liberada"), ("avisar", "avisado")])
def test_watch_reacts_when_a_spot_frees_up(settings, sent, on_free, expected_status):
    now = SLOT - timedelta(hours=5)  # ya abierto, aún no ha empezado
    job_id = make_job(settings, mode="observar", on_free=on_free)
    seed_slot(settings, free=1, now=now)  # foto recién tomada: no se consulta el portal
    started = []
    conn = connect(settings.data_dir)
    watch.watch_tick(conn, settings, now, started.append)

    assert jobs_repo.get_job(conn, job_id).status == expected_status
    if on_free == "reservar":
        assert started == [job_id]
    else:
        assert started == [] and sent[-1][1][0] == ("Reservar ahora", f"res:{job_id}")
    conn.close()


def test_watch_keeps_waiting_while_full(settings, sent):
    now = SLOT - timedelta(hours=5)
    job_id = make_job(settings, mode="observar")
    seed_slot(settings, free=0, now=now)
    conn = connect(settings.data_dir)
    watch.watch_tick(conn, settings, now, lambda _id: pytest.fail("no debería reservar"))
    assert jobs_repo.get_job(conn, job_id).status == "vigilando"
    conn.close()


# --- programador ----------------------------------------------------------------

def test_scheduler_starts_booking_one_minute_before_opening(settings):
    job_id = make_job(settings)
    worker = Worker(settings)
    started = []
    worker.start_booking = started.append

    worker.schedule_tick(OPENS - timedelta(minutes=10))
    conn = connect(settings.data_dir)
    assert jobs_repo.get_job(conn, job_id).status == "esperando_apertura" and started == []
    conn.close()

    worker.schedule_tick(OPENS - timedelta(seconds=59))
    assert started == [job_id]


def test_booking_imminent_window(settings):
    make_job(settings)
    conn = connect(settings.data_dir)
    assert not core_booking.booking_imminent(conn, settings, OPENS - timedelta(minutes=10))
    assert core_booking.booking_imminent(conn, settings, OPENS - timedelta(minutes=2))
    assert core_booking.booking_imminent(conn, settings, OPENS + timedelta(minutes=1))
    conn.close()


# --- lectura del carrito (HTML falso con la estructura verificada) -------------

def test_read_cart_from_portal_markup():
    html = (FIXTURES / "cart_page.html").read_text(encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        session = BookingSession.__new__(BookingSession)
        session.page = browser.new_page()
        session.page.set_content(html)
        cart = session.read_cart(datetime(2026, 10, 1).date(), time(14, 0))
        other = session.read_cart(datetime(2026, 10, 1).date(), time(18, 0))
        browser.close()
    assert cart == Cart(1, True, Decimal("4.00"), Decimal("20.00"), False, payment_options=3)
    assert not cart.covered_by_pass
    assert other.matches_slot is False


def test_small_helpers():
    assert parse_money("Saldo disponible 1.020,50 €") == Decimal("1020.50")
    assert parse_money("sin importe") is None
    assert spanish_date(datetime(2026, 10, 1).date()) == "1 de octubre de 2026"
    real = "Total 4,00 € account_balance_wallet Monedero Pago 4,00 € Operación 8075603820 Salir Añadir a mi calendario"
    ok = classify_confirmation("https://x/VentaServicios/CarritoResultado", real, None)
    assert ok.status == "reservado" and "8075603820" in ok.message
    assert classify_confirmation("https://x/CarritoResultado", "Algo raro", None).status == "revisar"
    assert classify_confirmation("https://x/Otra", real, None).status == "revisar"
    assert classify_confirmation("https://x/CarritoResultado", "No se ha podido completar", None).status == "fallido"
    # Con abono (02/10): «Confirmado» y «Carrito <número>», sin «Operación»
    pass_page = ("check_circle Confirmado Daoíz y Velarde Carrito 8125509951 viernes, 2 de octubre de 2026 "
                 "SALA MUSCULACION ADM USO LIBRE JOVEN 100% (ENTRADA) -5,00 € 0,00 € Total 0,00 € Salir")
    by_pass = classify_confirmation("https://x/VentaServicios/CarritoResultado", pass_page, None)
    assert by_pass.status == "reservado" and "carrito 8125509951" in by_pass.message
    assert classify_confirmation("https://x/CarritoResultado", "Carrito 8125509951", None).status == "revisar"


def test_clicks_earlier_when_portal_clock_is_ahead(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))

    class AheadPortal(FakeSession):
        def server_clock_offset(self):
            return 1.4  # el portal va entre 0,9 y 1,9 s por delante

    session = AheadPortal(clock)
    run(settings, make_job(settings), session, clock)
    assert session.click_times[0] == OPENS - timedelta(seconds=0.9)


def test_clock_lead_is_never_negative_nor_huge():
    assert core_booking.portal_clock_lead(None) == timedelta(0)
    assert core_booking.portal_clock_lead(-3) == timedelta(0)  # portal atrasado: se espera a nuestra T
    assert core_booking.portal_clock_lead(0.3) == timedelta(0)
    assert core_booking.portal_clock_lead(60) == core_booking.MAX_CLOCK_LEAD


def test_two_bookings_at_the_same_opening_run_in_parallel(settings, monkeypatch):
    """Dos perfiles, mismo turno y misma apertura: las dos reservas corren a la vez, no en fila."""
    import threading
    import time as real_time

    from src.worker import scheduler

    conn = connect(settings.data_dir)
    profiles_repo.create_profile(conn, "Novio", "#1d4ed8", "b@example.com", crypto.encrypt(settings, "x"))
    conn.commit()
    for pid in (1, 2):
        jobs_repo.create_job(conn, pid, 58, "Daoíz y Velarde", "Nado libre · Calle central", "piscina", SLOT, "reservar", None, False)
    conn.commit()
    conn.close()

    inside, overlap, lock = [], [], threading.Lock()

    def slow_booking(_settings, job_id, clock=None):
        with lock:
            inside.append(job_id)
            overlap.append(len(inside))
        real_time.sleep(0.3)
        with lock:
            inside.remove(job_id)
        return "reservado"

    monkeypatch.setattr(scheduler.core_booking, "run_booking", slow_booking)
    worker = Worker(settings)
    worker.schedule_tick(OPENS - timedelta(seconds=59))  # un solo tick lanza las dos
    deadline = real_time.monotonic() + 3
    while worker._running and real_time.monotonic() < deadline:
        real_time.sleep(0.05)
    assert max(overlap) == 2


def test_resumes_cart_when_portal_says_already_booked(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    msg = "x La sesión seleccionada no permite más de 1 reserva(s) por persona."
    session = FakeSession(clock, clicks=[AddOutcome(False, msg)])
    assert run(settings, make_job(settings), session, clock) == "reservado"
    assert session.opened_cart and session.confirmed
    assert len(session.click_times) == 1  # no insiste con un mensaje definitivo


def test_resumed_cart_with_other_things_is_not_paid(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    msg = "La sesión seleccionada no permite más de 1 reserva(s) por persona."
    session = FakeSession(clock, clicks=[AddOutcome(False, msg)], cart=Cart(2, False, Decimal("8.00"), Decimal("20.00"), False))
    assert run(settings, make_job(settings), session, clock) == "fallido"
    assert not session.confirmed


def test_saturated_portal_does_not_kill_the_booking(settings, sent):
    """Lo del 30/09 a las 18:00: el clic no responde, la lista desaparece y una recarga agota el tiempo."""
    from src.portal.errors import PortalError

    clock = FakeClock(OPENS - timedelta(seconds=60))

    class Saturated(FakeSession):
        def try_add_to_cart(self, at, activity):
            self.click_times.append(self.clock.now())
            n = len(self.click_times)
            self.clock.t += timedelta(seconds=10)
            if n == 1:
                return AddOutcome(False, "El portal no hizo nada al pulsar el turno.")
            if n == 2:
                return AddOutcome(False, "La lista de turnos no ha vuelto a cargar.", page_broken=True)
            if n == 3:
                raise PortalError("El portal tardó demasiado en responder.")
            return AddOutcome(True, "")

    session = Saturated(clock)
    assert run(settings, make_job(settings), session, clock) == "reservado"
    assert session.recoveries == 2  # tras la lista perdida y tras el error


def test_preparation_is_retried_if_portal_is_slow(settings, sent):
    from src.portal.errors import PortalError

    clock = FakeClock(OPENS - timedelta(seconds=60))

    class SlowPrepare(FakeSession):
        def prepare(self, service, center_id, day):
            self.prepare_calls = getattr(self, "prepare_calls", 0) + 1
            self.clock.t += timedelta(seconds=20)
            if self.prepare_calls == 1:
                raise PortalError("El portal tardó demasiado en responder.")
            self.prepared_at = self.clock.now()

    session = SlowPrepare(clock)
    assert run(settings, make_job(settings), session, clock) == "reservado"
    assert session.prepare_calls == 2
    assert session.click_times[0] == OPENS


def test_keeps_trying_while_the_portal_is_down_for_minutes(settings, sent):
    """Un lunes típico: el portal no responde durante 4 minutos y luego vuelve con plazas."""
    from src.portal.errors import PortalError

    clock = FakeClock(OPENS - timedelta(seconds=60))
    back_at = OPENS + timedelta(minutes=4)

    class DownThenBack(FakeSession):
        def try_add_to_cart(self, at, activity):
            self.click_times.append(self.clock.now())
            self.clock.t += timedelta(seconds=20)  # cada intento se come su tiempo
            if self.clock.now() < back_at:
                raise PortalError("El portal tardó demasiado en responder.")
            return AddOutcome(True, "")

    session = DownThenBack(clock)
    assert run(settings, make_job(settings), session, clock) == "reservado"
    # Espera creciente entre reintentos: no machaca el portal mientras está caído
    gaps = [(b - a).total_seconds() for a, b in zip(session.click_times, session.click_times[1:], strict=False)]
    assert gaps[0] < gaps[-1]
    assert len(session.click_times) < 20


def test_gives_up_when_the_window_ends(settings, sent):
    from src.portal.errors import PortalError

    clock = FakeClock(OPENS - timedelta(seconds=60))
    short = settings.model_copy(update={"booking_window_s": 120})

    class AlwaysDown(FakeSession):
        def try_add_to_cart(self, at, activity):
            self.click_times.append(self.clock.now())
            self.clock.t += timedelta(seconds=20)
            raise PortalError("El portal tardó demasiado en responder.")

    session = AlwaysDown(clock)
    assert run(short, make_job(settings), session, clock) == "fallido"
    assert session.click_times[-1] - OPENS <= timedelta(seconds=120)
    assert "en 2 min" in sent[-1][0]


# --- el carrito habla (30/09: un turno caducado en el carrito bloqueó la reserva de las 18:30) ---

EXPIRED = "La operación no se puede realizar porque el carrito ya está expirado."


def test_cart_message_opens_the_cart_and_follows_it(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    session = FakeSession(clock, clicks=[AddOutcome(False, EXPIRED, check_cart=True)])
    assert run(settings, make_job(settings), session, clock) == "reservado"
    assert session.opened_cart and session.confirmed
    _, attempts = job_of(settings, 1)
    assert ("carrito", "retomado") in [(a.action, a.result) for a in attempts]


def test_slot_that_silently_entered_the_cart_is_paid(settings, sent):
    """Lo del 30/09 a las 18:00: el portal tardó 24 s, no fue al carrito pero el contador subió."""
    clock = FakeClock(OPENS - timedelta(seconds=60))
    session = FakeSession(clock, clicks=[AddOutcome(False, "El portal no hizo nada al pulsar el turno.", check_cart=True)])
    assert run(settings, make_job(settings), session, clock) == "reservado"
    assert len(session.click_times) == 1


def test_blocking_cart_warns_once_and_keeps_trying(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))

    class OtherThingThenFree(FakeSession):
        def read_cart(self, day, at):
            return self.cart if self.clicks == [] else Cart(1, False, Decimal("4.00"), Decimal("20.00"), False)

    session = OtherThingThenFree(clock, clicks=[AddOutcome(False, EXPIRED, check_cart=True)] * 2 + [AddOutcome(True, "")])
    assert run(settings, make_job(settings), session, clock) == "reservado"
    assert session.recoveries == 2  # vuelve a la página del turno tras mirar el carrito
    warnings = [text for text, _ in sent if text.startswith("⚠️ Carrito pendiente")]
    assert len(warnings) == 1 and "no lo vacío" in warnings[0]


def test_slot_already_in_cart_before_opening_is_paid_without_clicking(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    session = FakeSession(clock)
    session.pending = 1
    assert run(settings, make_job(settings), session, clock) == "reservado"
    assert session.click_times == [] and session.confirmed


def test_other_things_in_cart_before_opening_warn_and_still_click_on_time(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    session = FakeSession(clock, cart=Cart(1, False, Decimal("4.00"), Decimal("20.00"), False))
    session.pending = 1
    assert run(settings, make_job(settings), session, clock) == "fallido"  # el carrito sigue sin ser este turno
    assert session.click_times[0] == OPENS
    assert not session.confirmed
    assert any(text.startswith("⚠️ Carrito pendiente") for text, _ in sent)


def test_dry_run_never_pays_a_cart_found_before_opening(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    session = FakeSession(clock)
    session.pending = 1
    assert run(settings, make_job(settings, dry_run=True), session, clock) == "prueba_ok"
    assert not session.confirmed


def test_closed_browser_stops_at_once(settings, sent):
    from src.portal.errors import BrowserClosed

    clock = FakeClock(OPENS - timedelta(seconds=60))

    class Closed(FakeSession):
        def try_add_to_cart(self, at, activity):
            self.click_times.append(self.clock.now())
            raise BrowserClosed("Se cerró el navegador del bot a mitad de la reserva.")

    session = Closed(clock)
    assert run(settings, make_job(settings), session, clock) == "fallido"
    assert len(session.click_times) == 1
    assert "Se cerró el navegador" in sent[-1][0]


def test_cart_badge_is_read_from_the_header():
    header = '<a href="../CarritoConfirmar" id="aCarrito"><span class="glyphicon"></span><span id="spnCarrito" class="badge">{}</span></a>'
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        session = BookingSession.__new__(BookingSession)
        session.page = browser.new_page()
        session.page.set_content(header.format(1))
        assert session.cart_count() == 1
        session.page.set_content('<a id="aCarrito"><span class="glyphicon"></span></a>')
        assert session.cart_count() == 0
        browser.close()


def test_closed_browser_error_is_recognised():
    from playwright.sync_api import Error as PlaywrightError

    from src.portal.booking import _portal_error
    from src.portal.errors import BrowserClosed

    assert isinstance(_portal_error(PlaywrightError("Locator.click: Target page, context or browser has been closed")), BrowserClosed)
    assert not isinstance(_portal_error(PlaywrightError("Timeout 45000ms exceeded")), BrowserClosed)


# --- abono de uso libre: el carrito no ofrece ninguna forma de pago ---------------

def test_pass_covered_cart_is_confirmed_without_payment_method(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    session = FakeSession(clock, cart=Cart(1, True, None, None, False, payment_options=0),
                          confirm=ConfirmResult("reservado", "Compra confirmada por el portal (carrito 8125509951).", None))
    assert run(settings, make_job(settings), session, clock) == "reservado"
    assert session.confirmed
    job, _ = job_of(settings, 1)
    assert job.result_message == "Confirmado con tu abono (sin pagar nada, carrito 8125509951)."


@pytest.mark.parametrize(
    ("cart", "text"),
    [
        (Cart(2, False, None, None, False, payment_options=0), "El carrito tiene 2 elementos"),
        (Cart(1, True, None, None, True, payment_options=0), "aceptar condiciones"),
        (Cart(1, True, Decimal("4.00"), None, False, payment_options=2), "El monedero no aparece"),  # tarjeta y Bizum
    ],
)
def test_pass_never_skips_the_other_checks(settings, sent, cart, text):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    session = FakeSession(clock, cart=cart)
    assert run(settings, make_job(settings), session, clock) == "fallido"
    assert not session.confirmed and text in sent[-1][0]


def test_read_cart_without_payment_methods_means_pass():
    html = (FIXTURES / "cart_page.html").read_text(encoding="utf-8")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        session = BookingSession.__new__(BookingSession)
        session.page = browser.new_page()
        session.page.set_content(html)
        session.page.evaluate("document.querySelector('#ContentFixedSection_uCarritoConfirmar_divPaymentMethods').remove()")
        cart = session.read_cart(datetime(2026, 10, 1).date(), time(14, 0))
        browser.close()
    assert cart.covered_by_pass and cart.items == 1 and cart.matches_slot


@pytest.mark.parametrize(("profiles", "expected_overlap"), [((1, 1), 1), ((1, 2), 2)])
def test_one_booking_at_a_time_per_profile(settings, sent, monkeypatch, profiles, expected_overlap):
    """El carrito y la sesión del portal son de la persona: dos reservas suyas a la vez se pisan."""
    import threading
    import time as real_time

    import src.core.booking as mod

    conn = connect(settings.data_dir)
    profiles_repo.create_profile(conn, "Novio", "#1d4ed8", "b@example.com", crypto.encrypt(settings, "clave-portal"))
    ids = [jobs_repo.create_job(conn, pid, 58, "Daoíz y Velarde", "Nado libre · Calle central", "piscina",
                                SLOT, "reservar", None, False) for pid in profiles]
    conn.commit()
    conn.close()

    inside, overlap, lock = [], [], threading.Lock()

    class Tracked(FakeSession):
        def __enter__(self):
            with lock:
                inside.append(self)
                overlap.append(len(inside))
            real_time.sleep(0.2)
            return self

        def __exit__(self, *exc):
            with lock:
                inside.remove(self)
            return False

    monkeypatch.setattr(mod, "BookingSession", lambda *a, **k: Tracked(FakeClock(OPENS)))
    threads = [threading.Thread(target=core_booking.run_booking, args=(settings, job_id)) for job_id in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert max(overlap) == expected_overlap


def test_expired_cart_is_discarded_and_clicking_goes_on(settings, sent):
    """Lo del 02/10: el carrito caducado, al abrirlo, sale como «Expirado» y el portal lo descarta."""
    clock = FakeClock(OPENS - timedelta(seconds=60))

    class ExpiredOnce(FakeSession):
        def open_cart(self):
            self.cart_page, page = "listo", self.cart_page
            return page

    session = ExpiredOnce(clock, clicks=[AddOutcome(False, "No se permiten más de 1 reservas por persona para cada día.",
                                                    check_cart=True), AddOutcome(True, "")])
    session.cart_page = "expirado"
    assert run(settings, make_job(settings), session, clock) == "reservado"
    assert len(session.click_times) == 2
    assert not any(text.startswith("⚠️ Carrito pendiente") for text, _ in sent)  # nada que avisar


def test_already_booked_with_expired_cart_keeps_trying(settings, sent):
    clock = FakeClock(OPENS - timedelta(seconds=60))
    msg = "La sesión seleccionada no permite más de 1 reserva(s) por persona."

    class ExpiredOnce(FakeSession):
        def open_cart(self):
            self.cart_page, page = "listo", self.cart_page
            return page

    session = ExpiredOnce(clock, clicks=[AddOutcome(False, msg), AddOutcome(True, "")])
    session.cart_page = "expirado"
    assert run(settings, make_job(settings), session, clock) == "reservado"


def test_expired_result_page_is_never_a_success():
    page = "cancel Expirado Daoíz y Velarde Carrito 8125509514 SALA MUSCULACION Total 0,00 € Salir"
    assert classify_confirmation("https://x/VentaServicios/CarritoResultado", page, None).status == "fallido"
