"""Trabajo en segundo plano dentro del proceso de la web (una sola Raspberry, un solo proceso).

Tres hilos:
    programador   cada TICK: lanza cada reserva 60 s antes de su apertura (o ya, si está abierta),
                  y termina lo que ya ha pasado
    vigilante     cada watch_interval: vigilancias de plazas (modo observar)
    telegram      escucha los botones de los avisos y los «/start» de vinculación (long polling)

Cada reserva corre en su propio hilo para que las de perfiles distintos a la misma hora no se
esperen (las de un mismo perfil van una detrás de otra: ver core/booking.py).
"""

import logging
import threading
from datetime import datetime
from functools import partial

from src.config import Settings
from src.core import booking as core_booking
from src.core import watch as core_watch
from src.core.telegram_actions import handle_callback
from src.db import jobs as jobs_repo
from src.db.database import connect
from src.notify import linking
from src.notify.telegram import LONG_POLL_S, TelegramError

log = logging.getLogger(__name__)

# Los tests lo apagan (tests/conftest.py)
ENABLED = True
TICK_S = 2
TELEGRAM_RETRY_S = 30


class Worker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._stop = threading.Event()
        self._running: set[int] = set()
        self._running_lock = threading.Lock()
        self.telegram_listening = False

    # --- reservas ------------------------------------------------------------

    def start_booking(self, job_id: int) -> bool:
        """Lanza la reserva en su propio hilo, salvo que ya esté en marcha."""
        with self._running_lock:
            if job_id in self._running:
                return False
            self._running.add(job_id)
        threading.Thread(target=self._booking_thread, args=(job_id,), name=f"reserva-{job_id}", daemon=True).start()
        return True

    def _booking_thread(self, job_id: int) -> None:
        try:
            status = core_booking.run_booking(self.settings, job_id)
            log.info("Reserva %s terminada: %s", job_id, status)
        finally:
            with self._running_lock:
                self._running.discard(job_id)

    # --- hilos ---------------------------------------------------------------

    def start(self) -> None:
        self._recover()
        for target, name in ((self._schedule_loop, "programador"), (self._watch_loop, "vigilante")):
            threading.Thread(target=target, name=name, daemon=True).start()
        if self.settings.telegram_bot_token:
            threading.Thread(target=self._telegram_loop, name="telegram", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _recover(self) -> None:
        """Si la web se reinició a mitad de una reserva, se vuelve a programar."""
        conn = connect(self.settings.data_dir)
        try:
            for job in jobs_repo.list_active(conn):
                if job.status == "reservando":
                    jobs_repo.set_status(conn, job.id, "esperando_apertura", "Reanudada tras reiniciar el bot.")
            conn.commit()
        finally:
            conn.close()

    def _schedule_loop(self) -> None:
        while not self._stop.wait(TICK_S):
            try:
                self.schedule_tick(datetime.now(self.settings.zone))
            except Exception:
                log.exception("Error en el programador")

    def schedule_tick(self, now: datetime) -> None:
        conn = connect(self.settings.data_dir)
        try:
            core_watch.expire_past(conn, self.settings, now)
            for job in jobs_repo.list_active(conn):
                if job.mode != "reservar" or job.status == "reservando":
                    continue
                opens = core_booking.opening_of(self.settings, job)
                if job.status == "pendiente" and now < opens - core_booking.PREPARE_BEFORE:
                    jobs_repo.set_status(conn, job.id, "esperando_apertura", f"Abre el {opens:%d/%m a las %H:%M}.")
                    conn.commit()
                if now >= opens - core_booking.PREPARE_BEFORE:
                    self.start_booking(job.id)
        finally:
            conn.close()

    def _watch_loop(self) -> None:
        interval = core_watch.watch_interval(self.settings).total_seconds()
        while not self._stop.wait(interval):
            conn = connect(self.settings.data_dir)
            try:
                core_watch.watch_tick(conn, self.settings, datetime.now(self.settings.zone), self.start_booking)
            except Exception:
                log.exception("Error en la vigilancia")
            finally:
                conn.close()

    def _telegram_loop(self) -> None:
        self.telegram_listening = True
        try:
            while not self._stop.is_set():
                conn = connect(self.settings.data_dir)
                try:
                    linking.process_updates(
                        conn,
                        self.settings,
                        datetime.now(self.settings.zone),
                        on_callback=partial(self._on_callback, conn),
                        wait_s=LONG_POLL_S,
                    )
                except TelegramError as exc:
                    if "409" in str(exc):  # otro proceso escucha con el mismo token (p. ej. la Raspberry)
                        log.warning("Telegram: otra copia del bot está usando este mismo token; los botones "
                                    "de los avisos pueden llegarle a ella. Deja solo una encendida.")
                    else:
                        log.warning("Telegram: %s", exc)
                    self._stop.wait(TELEGRAM_RETRY_S)
                except Exception:
                    log.exception("Error escuchando Telegram")
                    self._stop.wait(TELEGRAM_RETRY_S)
                finally:
                    conn.close()
        finally:
            self.telegram_listening = False

    def _on_callback(self, conn, callback) -> None:
        handle_callback(conn, self.settings, callback, self.start_booking)


_worker: Worker | None = None


def start_worker(settings: Settings) -> Worker | None:
    global _worker
    if not ENABLED:
        return None
    if _worker is None:
        _worker = Worker(settings)
        _worker.start()
    return _worker


def current_worker() -> Worker | None:
    return _worker
