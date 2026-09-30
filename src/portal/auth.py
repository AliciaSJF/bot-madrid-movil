"""Login en el portal y reutilización de la sesión guardada.

Reglas: un solo intento de login por llamada, sin bucles; si el portal rechaza las
credenciales, pide verificación o muestra un captcha, se para y se avisa.
"""

import time
from dataclasses import dataclass

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from src.config import Settings
from src.portal import selectors as sel
from src.portal.browser import open_context, session_file
from src.portal.errors import CaptchaDetected, LoginRejected, PortalError, VerificationRequired

LOGIN_RESULT_TIMEOUT_S = 20


@dataclass(frozen=True)
class SessionResult:
    reused: bool  # True si valía la sesión guardada y no hizo falta iniciar sesión

    @property
    def message(self) -> str:
        return "Sesión guardada todavía válida." if self.reused else "Sesión iniciada correctamente."


def ensure_session(settings: Settings, profile_id: int, username: str, password: str) -> SessionResult:
    """Deja una sesión válida guardada para el perfil. Lanza un PortalError si no lo consigue."""
    state = session_file(settings, profile_id)
    state.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open_context(settings, state) as context:
            page = context.new_page()
            if state.exists() and session_is_valid(page):
                return SessionResult(reused=True)
            login(page, username, password)
            context.storage_state(path=state)
            return SessionResult(reused=False)
    except PlaywrightError as exc:
        # El mensaje de Playwright puede incluir el valor escrito en un campo: no se propaga
        raise PortalError(f"Fallo del navegador ({type(exc).__name__})") from None


def session_is_valid(page: Page) -> bool:
    page.goto(sel.HOME_URL, wait_until="domcontentloaded")
    if sel.LOGIN_PATH in page.url:
        return False
    return page.locator(sel.GUEST_LOGIN_LINK).count() == 0


def login(page: Page, username: str, password: str) -> None:
    page.goto(sel.LOGIN_URL, wait_until="domcontentloaded")
    _stop_if_captcha(page)

    page.locator(sel.LOGIN_OPTION_EMAIL).first.click()
    page.locator(sel.USERNAME_INPUT).fill(username)
    page.locator(sel.PASSWORD_INPUT).fill(password)
    keep = page.locator(sel.KEEP_SESSION_CHECKBOX)
    if keep.count() and not keep.is_checked():
        keep.check()
    page.locator(sel.LOGIN_BUTTON).click()

    _wait_for_login_result(page)


def _wait_for_login_result(page: Page) -> None:
    """Espera a uno de los desenlaces: Home, aviso de error, verificación o captcha."""
    deadline = time.monotonic() + LOGIN_RESULT_TIMEOUT_S
    while time.monotonic() < deadline:
        if sel.HOME_PATH in page.url:
            return
        if page.get_by_text(sel.LOGIN_ERROR_TEXT).count():
            raise LoginRejected("El portal dice que el usuario o la contraseña no son válidos.")
        if _verification_requested(page):
            raise VerificationRequired("El portal pide una verificación adicional. Entra una vez a mano.")
        _stop_if_captcha(page)
        page.wait_for_timeout(250)
    raise PortalError("El portal no respondió al login a tiempo.")


def _verification_requested(page: Page) -> bool:
    for selector in sel.LOGIN_VERIFICATION_PANELS:
        panel = page.locator(selector)
        if panel.count() and panel.first.inner_text().strip():
            return True
    return False


def _stop_if_captcha(page: Page) -> None:
    if page.locator(sel.CAPTCHA).count():
        raise CaptchaDetected("El portal muestra un captcha. El bot no lo resuelve.")
