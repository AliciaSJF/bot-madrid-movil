"""Guardar páginas del portal (HTML + captura) para estudiar pasos que aún no conocemos.

Herramienta de desarrollo. Lo guardado contiene datos personales y de sesión:
va a data/capturas/ (ignorado por git) y NO se usa como fixture sin sanear.
"""

import re
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError

from src.config import Settings
from src.portal import selectors as sel
from src.portal.auth import session_is_valid
from src.portal.browser import open_context, session_file
from src.portal.errors import PortalError


def capture_pages(settings: Settings, profile_id: int, paths: list[str]) -> Path:
    """Abre cada ruta del portal con la sesión guardada del perfil y guarda HTML, captura y enlaces."""
    state = session_file(settings, profile_id)
    if not state.exists():
        raise PortalError("Este perfil no tiene sesión guardada: prueba antes la conexión.")

    out_dir = settings.data_dir / "capturas" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with open_context(settings, state) as context:
            page = context.new_page()
            if not session_is_valid(page):
                raise PortalError("La sesión guardada ha caducado: prueba otra vez la conexión.")
            for path in paths:
                if not path.startswith("/"):
                    raise ValueError(f"Ruta del portal no válida: {path}")
                page.goto(sel.BASE_URL + path, wait_until="networkidle")
                name = _slug(path)
                (out_dir / f"{name}.html").write_text(page.content(), encoding="utf-8")
                page.screenshot(path=out_dir / f"{name}.png", full_page=True)
                links = page.eval_on_selector_all(
                    "a", "els => els.map(a => (a.innerText.trim() + '\\t' + (a.getAttribute('href') || '')))"
                )
                (out_dir / f"{name}.links.txt").write_text("\n".join(links), encoding="utf-8")
    except PlaywrightError as exc:
        raise PortalError(f"Fallo del navegador ({type(exc).__name__})") from None
    return out_dir


def _slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", path.split("?")[0]).strip("_") or "raiz"
