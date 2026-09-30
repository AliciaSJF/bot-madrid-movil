"""Avisos a cada perfil (de momento, por Telegram)."""

import logging
import sqlite3

from src.config import Settings
from src.db import profiles as profiles_repo
from src.notify import linking
from src.notify.telegram import TelegramError

log = logging.getLogger(__name__)


def notify(
    conn: sqlite3.Connection,
    settings: Settings,
    profile_id: int,
    text: str,
    buttons: list[tuple[str, str]] | None = None,
) -> bool:
    """Envía un aviso al Telegram del perfil. Devuelve False si no tiene Telegram o falla el envío.

    Nunca lanza: un aviso fallido no debe interrumpir una reserva. El texto no debe llevar
    contraseñas, cookies ni datos de pago.
    """
    profile = profiles_repo.get_profile(conn, profile_id)
    if profile is None or not profile.telegram_linked:
        return False
    try:
        linking.client(settings).send_message(profile.telegram_chat_id, text, buttons)
        return True
    except TelegramError as exc:
        log.warning("No se pudo avisar al perfil %s por Telegram: %s", profile_id, exc)
        return False
