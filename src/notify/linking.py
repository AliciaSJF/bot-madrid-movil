"""Vincular un perfil con su chat de Telegram, sin copiar identificadores a mano.

1. start_link(): genera un código de un solo uso (15 min) y el enlace t.me/<bot>?start=<código>.
2. La persona abre el enlace en su móvil y pulsa "Iniciar": Telegram envía "/start <código>" al bot.
3. process_updates(): lee los mensajes pendientes (getUpdates) y guarda el chat en el perfil del código.
"""

import secrets
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta

from src.config import Settings
from src.db import profiles as profiles_repo
from src.db.database import get_setting, set_setting
from src.notify.telegram import CallbackQuery, TelegramClient

LINK_TTL = timedelta(minutes=15)
OFFSET_KEY = "telegram_update_offset"
BOT_USERNAME_KEY = "telegram_bot_username"


def client(settings: Settings) -> TelegramClient:
    token = settings.telegram_bot_token.get_secret_value() if settings.telegram_bot_token else ""
    return TelegramClient(token)


def bot_username(conn: sqlite3.Connection, settings: Settings) -> str:
    """Usuario del bot (getMe), guardado para no preguntarlo en cada visita."""
    username = get_setting(conn, BOT_USERNAME_KEY)
    if username is None:
        username = client(settings).bot_username()
        set_setting(conn, BOT_USERNAME_KEY, username)
        conn.commit()
    return username


def start_link(conn: sqlite3.Connection, settings: Settings, profile_id: int, now: datetime) -> str:
    """Prepara la vinculación y devuelve el enlace que hay que abrir en el móvil."""
    username = bot_username(conn, settings)
    code = secrets.token_urlsafe(9)  # 12 caracteres válidos para ?start=
    profiles_repo.set_telegram_link_code(conn, profile_id, code, now + LINK_TTL)
    conn.commit()
    return f"https://t.me/{username}?start={code}"


def process_updates(
    conn: sqlite3.Connection,
    settings: Settings,
    now: datetime,
    on_callback: Callable[[CallbackQuery], None] | None = None,
    wait_s: int = 0,
) -> list[int]:
    """Procesa lo pendiente: «/start» (vinculación) y pulsaciones de botones (on_callback).

    Devuelve los perfiles que se han vinculado ahora.
    """
    telegram = client(settings)
    offset = get_setting(conn, OFFSET_KEY)
    updates, next_offset = telegram.get_updates(int(offset) if offset else None, wait_s)

    linked = []
    for start in updates:
        if isinstance(start, CallbackQuery):
            if on_callback is not None:
                on_callback(start)
            continue
        profile_id = profiles_repo.find_by_link_code(conn, start.payload, now) if start.payload else None
        if profile_id is None:
            telegram.send_message(
                start.chat_id,
                "Hola 👋 Para recibir avisos, entra en la web del bot → Perfil → «Vincular Telegram».",
            )
            continue
        profiles_repo.set_telegram_chat(conn, profile_id, start.chat_id)
        profile = profiles_repo.get_profile(conn, profile_id)
        telegram.send_message(
            start.chat_id,
            f"✅ Vinculado con el perfil «{profile.display_name}». Aquí te avisaré de tus reservas.",
        )
        linked.append(profile_id)

    if next_offset is not None:
        set_setting(conn, OFFSET_KEY, str(next_offset))
    conn.commit()
    return linked
