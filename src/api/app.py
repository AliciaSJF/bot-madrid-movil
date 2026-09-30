"""Montaje de la web: middleware, estáticos y routers. La lógica de cada pantalla vive en routers/.

Arranque: python -m scripts.cli web  (o uvicorn --factory src.api.app:create_app)
"""

import secrets

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from src.api.deps import Redirect, redirect
from src.api.routers import account, booking, house, jobs, profiles, pwa
from src.api.templating import STATIC_DIR
from src.config import Settings, get_settings
from src.db.database import connect, get_setting, init_db, set_setting

SESSION_SECRET_KEY = "session_secret"
SESSION_MAX_AGE = 60 * 24 * 3600  # 60 días: en el móvil casi nunca se vuelve a pedir


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    init_db(settings.data_dir)

    app = FastAPI(title="Bot Deportes Madrid", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings

    app.add_middleware(
        SessionMiddleware,
        secret_key=_session_secret(settings),
        session_cookie="bmm_session",
        max_age=SESSION_MAX_AGE,
        same_site="lax",
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    for router in (pwa.router, house.router, profiles.router, account.router, booking.router, jobs.router):
        app.include_router(router)

    @app.exception_handler(Redirect)
    async def handle_redirect(_request: Request, exc: Redirect) -> Response:
        return redirect(exc.url)

    return app


def _session_secret(settings: Settings) -> str:
    """Secreto de las cookies: se genera la primera vez y se guarda en la BD."""
    conn = connect(settings.data_dir)
    try:
        secret = get_setting(conn, SESSION_SECRET_KEY)
        if secret is None:
            secret = secrets.token_urlsafe(32)
            set_setting(conn, SESSION_SECRET_KEY, secret)
            conn.commit()
        return secret
    finally:
        conn.close()
