"""Login contra una página falsa servida con page.route(): nunca se conecta al portal real."""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from src.portal import selectors as sel
from src.portal.auth import login, session_is_valid
from src.portal.errors import LoginRejected, VerificationRequired

FIXTURES = Path(__file__).parent / "fixtures"
LOGIN_HTML = (FIXTURES / "login_page.html").read_text(encoding="utf-8")
HOME_LOGGED_IN = "<html><body><a href='#'>Cerrar sesión</a></body></html>"
HOME_GUEST = "<html><body><a href=\"javascript:__doPostBack('x', 'IniciarSesion')\">Iniciar sesión</a></body></html>"


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        yield b
        b.close()


def fake_portal(browser, home_html=HOME_LOGGED_IN):
    context = browser.new_context()
    context.set_default_timeout(5_000)

    def handle(route):
        path = route.request.url.removeprefix(sel.BASE_URL)
        if path.startswith(sel.LOGIN_PATH):
            route.fulfill(body=LOGIN_HTML, content_type="text/html; charset=utf-8")
        elif path.startswith(sel.HOME_PATH):
            route.fulfill(body=home_html, content_type="text/html; charset=utf-8")
        else:
            route.abort()

    context.route(f"{sel.BASE_URL}/**", handle)
    return context, context.new_page()


def test_login_success_goes_home_and_keeps_session(browser):
    context, page = fake_portal(browser)
    login(page, "ok@example.com", "x")  # la página falsa exige marcar "No cerrar sesión"
    assert sel.HOME_PATH in page.url
    context.close()


def test_login_rejected(browser):
    context, page = fake_portal(browser)
    with pytest.raises(LoginRejected):
        login(page, "mal@example.com", "x")
    context.close()


def test_login_verification(browser):
    context, page = fake_portal(browser)
    with pytest.raises(VerificationRequired):
        login(page, "verifica@example.com", "x")
    context.close()


def test_session_is_valid(browser):
    context, page = fake_portal(browser, HOME_LOGGED_IN)
    assert session_is_valid(page)
    context.close()

    context, page = fake_portal(browser, HOME_GUEST)
    assert not session_is_valid(page)
    context.close()


def test_network_error_on_load_is_retried_once(browser):
    from src.portal.browser import goto

    context = browser.new_context()
    calls = {"n": 0}

    def flaky(route):
        calls["n"] += 1
        if calls["n"] == 1:
            route.abort("internetdisconnected")
        else:
            route.fulfill(body="<p>ok</p>", content_type="text/html; charset=utf-8")

    context.route(f"{sel.BASE_URL}/**", flaky)
    page = context.new_page()
    goto(page, sel.LOGIN_URL)
    assert calls["n"] == 2
    context.close()


def test_error_description_hides_secrets():
    from playwright.sync_api import Error as PlaywrightError

    from src.portal.browser import describe_error

    msg = describe_error(PlaywrightError("Locator.fill: algo con clave-secreta y yo@example.com\nCall log: ..."),
                         "yo@example.com", "clave-secreta")
    assert "clave-secreta" not in msg and "yo@example.com" not in msg

    net = describe_error(PlaywrightError("Page.goto: net::ERR_NETWORK_CHANGED at https://x"))
    assert "ERR_NETWORK_CHANGED" in net and "Vuelve a probar" in net
