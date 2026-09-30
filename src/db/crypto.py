"""Cifrado de las credenciales del portal. La clave vive solo en FERNET_KEY."""

from cryptography.fernet import Fernet, InvalidToken

from src.config import Settings


class MissingKeyError(RuntimeError):
    pass


def _fernet(settings: Settings) -> Fernet:
    if settings.fernet_key is None:
        raise MissingKeyError("Falta FERNET_KEY en .env")
    return Fernet(settings.fernet_key.get_secret_value().encode())


def encrypt(settings: Settings, plaintext: str) -> str:
    return _fernet(settings).encrypt(plaintext.encode()).decode()


def decrypt(settings: Settings, token: str) -> str:
    try:
        return _fernet(settings).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        # Sin el texto cifrado en el mensaje, para no filtrarlo en logs
        raise ValueError("No se pudo descifrar la credencial: ¿ha cambiado FERNET_KEY?") from exc
