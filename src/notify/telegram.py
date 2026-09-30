"""Cliente mínimo de la API de bots de Telegram (stdlib, sin dependencias).

El token va en la URL de la API: nunca se registra ni aparece en los mensajes de error.
Sin webhook (la web no está expuesta a internet): las actualizaciones se leen con getUpdates.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT_S = 15
LONG_POLL_S = 25


class TelegramError(Exception):
    """Fallo hablando con Telegram. El mensaje nunca incluye el token."""


@dataclass(frozen=True)
class StartMessage:
    update_id: int
    chat_id: int
    payload: str  # lo que va tras "/start " (el código de vinculación), o ""
    first_name: str


@dataclass(frozen=True)
class CallbackQuery:
    """Pulsación de un botón de un mensaje del bot."""

    update_id: int
    callback_id: str
    chat_id: int
    message_id: int
    data: str


class TelegramClient:
    def __init__(self, token: str):
        if not token or ":" not in token:
            raise TelegramError("Falta TELEGRAM_BOT_TOKEN en .env o no tiene el formato del de BotFather.")
        self._token = token

    def _call(self, method: str, http_timeout: float = TIMEOUT_S, **params) -> object:
        # http_timeout es de esta petición; «timeout» dentro de params es el parámetro de la API de Telegram
        data = json.dumps(params).encode()
        request = urllib.request.Request(
            API.format(token=self._token, method=method),
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=http_timeout) as response:
                body = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.load(exc).get("description", "")
            except (ValueError, AttributeError):
                pass
            raise TelegramError(f"Telegram respondió {exc.code} {detail}".strip()) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TelegramError(f"No se pudo contactar con Telegram ({type(exc).__name__}).") from None
        if not body.get("ok"):
            raise TelegramError(f"Telegram rechazó la petición: {body.get('description', '')}")
        return body["result"]

    def bot_username(self) -> str:
        return self._call("getMe")["username"]

    def send_message(self, chat_id: int, text: str, buttons: list[tuple[str, str]] | None = None) -> None:
        """buttons: [(texto, dato)] en una fila; el dato vuelve en la pulsación (máx. 64 bytes)."""
        params = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in buttons]]}
        self._call("sendMessage", **params)

    def answer_callback(self, callback_id: str, text: str) -> None:
        self._call("answerCallbackQuery", callback_query_id=callback_id, text=text)

    def clear_buttons(self, chat_id: int, message_id: int) -> None:
        self._call(
            "editMessageReplyMarkup", chat_id=chat_id, message_id=message_id, reply_markup={"inline_keyboard": []}
        )

    def get_updates(self, offset: int | None, wait_s: int = 0) -> tuple[list, int | None]:
        """Actualizaciones pendientes («/start …» y pulsaciones de botones).

        Con wait_s > 0 espera en Telegram hasta que llegue algo (long polling). Devuelve también
        el offset para marcar como leídas todas las recibidas.
        """
        params = {"timeout": wait_s, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        updates = self._call("getUpdates", http_timeout=wait_s + TIMEOUT_S, **params)
        parsed: list[StartMessage | CallbackQuery] = []
        next_offset = offset
        for update in updates:
            next_offset = update["update_id"] + 1
            if "callback_query" in update:
                cb = update["callback_query"]
                message = cb.get("message") or {}
                parsed.append(
                    CallbackQuery(
                        update["update_id"], cb["id"], message.get("chat", {}).get("id", 0),
                        message.get("message_id", 0), cb.get("data", ""),
                    )
                )
                continue
            message = update.get("message") or {}
            text = (message.get("text") or "").strip()
            if message.get("chat", {}).get("type") != "private" or not text.startswith("/start"):
                continue
            payload = text.removeprefix("/start").strip()
            parsed.append(
                StartMessage(update["update_id"], message["chat"]["id"], payload, message["chat"].get("first_name", ""))
            )
        return parsed, next_offset
