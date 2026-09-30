"""Qué hacer cuando se pulsa un botón de un aviso de Telegram.

    obs:<job>  «Observar»: vigilar el turno de una reserva que no salió (reserva si se libera)
    res:<job>  «Reservar ahora»: tras un aviso de plaza liberada
    ign:<job>  «Ignorar»: solo quita los botones

Solo vale el botón pulsado desde el Telegram del perfil dueño de la programación.
"""

import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime

from src.config import Settings
from src.core.booking import slot_label
from src.db import jobs as jobs_repo
from src.db import profiles as profiles_repo
from src.notify import linking
from src.notify.telegram import CallbackQuery, TelegramError

log = logging.getLogger(__name__)


def handle_callback(
    conn: sqlite3.Connection, settings: Settings, cb: CallbackQuery, start_booking: Callable[[int], None]
) -> str:
    """Devuelve el texto con el que se responde a la pulsación."""
    reply = _decide(conn, settings, cb, start_booking)
    telegram = linking.client(settings)
    try:
        telegram.answer_callback(cb.callback_id, reply)
        telegram.clear_buttons(cb.chat_id, cb.message_id)
        if reply.startswith(("👀", "⏳")):
            telegram.send_message(cb.chat_id, reply)
    except TelegramError as exc:
        log.warning("No se pudo responder al botón de Telegram: %s", exc)
    return reply


def _decide(conn, settings, cb: CallbackQuery, start_booking) -> str:
    action, _, raw_id = cb.data.partition(":")
    job = jobs_repo.get_job(conn, int(raw_id)) if raw_id.isdigit() else None
    if job is None:
        return "Esa programación ya no existe."
    profile = profiles_repo.get_profile(conn, job.profile_id)
    if profile is None or profile.telegram_chat_id != cb.chat_id:
        return "Este botón no es de tu perfil."
    if job.slot_at <= datetime.now(job.slot_at.tzinfo):
        return "Ese turno ya ha empezado."

    if action == "ign":
        return "Vale, lo dejo."
    if action == "obs":
        watch_id = jobs_repo.create_watch_from(conn, job)
        jobs_repo.add_attempt(conn, job.id, "telegram", "observar", f"vigilancia #{watch_id}")
        conn.commit()
        return f"👀 Observando: si se libera una plaza, la reservo.\n{slot_label(settings, job)}"
    if action == "res":
        if job.status != "avisado":
            return "Ese aviso ya no está pendiente."
        start_booking(job.id)
        return f"⏳ Intentando reservar ahora…\n{slot_label(settings, job)}"
    return "Botón desconocido."
