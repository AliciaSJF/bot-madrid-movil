"""Guardar páginas del portal (HTML + captura) para estudiar pasos que aún no conocemos.

Herramienta de desarrollo. Lo guardado contiene datos personales y de sesión:
va a data/capturas/ (ignorado por git) y NO se usa como fixture sin sanear.

Solo navega: abre rutas o pulsa tarjetas de menú por su título. Nunca confirma nada.
"""

import re
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from src.config import Settings
from src.portal import selectors as sel
from src.portal.auth import session_is_valid
from src.portal.browser import describe_error, goto, open_context, session_file
from src.portal.errors import PortalError

AFTER_CLICK_WAIT_MS = 4_000

# Textos que nunca se pulsan desde el capturador, por si coinciden con un botón de acción
FORBIDDEN_CLICKS = re.compile(r"reserv|confirm|pagar|comprar|anular|aceptar|acepto", re.IGNORECASE)


def capture_pages(settings: Settings, profile_id: int, paths: list[str], clicks: list[str] | None = None) -> Path:
    """Con la sesión guardada del perfil: abre cada ruta y, tras la última, pulsa las tarjetas
    indicadas en orden. Guarda HTML, captura, enlaces y URL de cada página."""
    state = session_file(settings, profile_id)
    if not state.exists():
        raise PortalError("Este perfil no tiene sesión guardada: prueba antes la conexión.")
    for text in clicks or []:
        if FORBIDDEN_CLICKS.search(text):
            raise ValueError(f"El capturador no pulsa «{text}»: podría ser una acción, no un menú.")

    out_dir = settings.data_dir / "capturas" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with open_context(settings, state) as context:
            page = context.new_page()
            if not session_is_valid(page):
                raise PortalError("La sesión guardada ha caducado: prueba otra vez la conexión.")
            step = 0
            for path in paths:
                if not path.startswith("/"):
                    raise ValueError(f"Ruta del portal no válida: {path}")
                goto(page, sel.BASE_URL + path, wait_until="networkidle")
                step += 1
                _save(page, out_dir, f"{step:02d}_{_slug(path)}")
            for text in clicks or []:
                _click_card(page, text)
                step += 1
                _save(page, out_dir, f"{step:02d}_{_slug(text)}")
    except PlaywrightError as exc:
        raise PortalError(describe_error(exc)) from None
    return out_dir


def _click_card(page: Page, title: str) -> None:
    """Pulsa una tarjeta de menú (article con un h4 cuyo title es exactamente ese texto)."""
    card = page.locator(f"article.navigation-section-widget-collection-item:has(h4[title='{title}'])")
    if card.count() == 0:
        # Otras pantallas pueden usar otro tipo de elemento: probar por texto visible exacto
        card = page.get_by_text(title, exact=True)
    card.first.click()
    page.wait_for_load_state("networkidle")
    # El contenido llega por postbacks parciales después de "networkidle": margen extra
    page.wait_for_timeout(AFTER_CLICK_WAIT_MS)


def _save(page: Page, out_dir: Path, name: str) -> None:
    (out_dir / f"{name}.html").write_text(page.content(), encoding="utf-8")
    (out_dir / f"{name}.url.txt").write_text(page.url, encoding="utf-8")
    page.screenshot(path=out_dir / f"{name}.png", full_page=True)
    links = page.eval_on_selector_all(
        "a", "els => els.map(a => (a.innerText.trim() + '\\t' + (a.getAttribute('href') || '')))"
    )
    (out_dir / f"{name}.links.txt").write_text("\n".join(links), encoding="utf-8")


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text.split("?")[0]).strip("_")[:40] or "raiz"
