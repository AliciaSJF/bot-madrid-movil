"""Web móvil: cuenta de casa, perfiles, reservar y mis reservas.

Arranque: python -m scripts.cli web  (o uvicorn --factory src.api.app:create_app)
"""

import secrets
import sqlite3
import time
from collections.abc import Iterator
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from src.api.auth import hash_password, verify_password
from src.config import Settings, get_settings
from src.db import crypto
from src.db import jobs as jobs_repo
from src.db import profiles as profiles_repo
from src.db.database import connect, get_setting, init_db, set_setting

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

HOUSE_PASSWORD_KEY = "house_password_hash"
SESSION_SECRET_KEY = "session_secret"
SESSION_MAX_AGE = 60 * 24 * 3600  # 60 días: en el móvil casi nunca se vuelve a pedir
MIN_HOUSE_PASSWORD = 8

WEEKDAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

StrForm = Annotated[str, Form()]


class Redirect(Exception):
    """Se lanza desde las comprobaciones de acceso para mandar a otra pantalla."""

    def __init__(self, url: str):
        self.url = url


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _session_secret(settings: Settings) -> str:
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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    init_db(settings.data_dir)

    app = FastAPI(title="Bot Deportes Madrid", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=_session_secret(settings),
        session_cookie="bmm_session",
        max_age=SESSION_MAX_AGE,
        same_site="lax",
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    templates = Jinja2Templates(directory=BASE_DIR / "templates")

    def fmt_slot(dt: datetime) -> str:
        local = dt.astimezone(settings.zone)
        return f"{WEEKDAYS[local.weekday()]} {local.day} {MONTHS[local.month - 1]} · {local:%H:%M}"

    templates.env.filters["slot"] = fmt_slot
    templates.env.globals.update(
        SERVICES=jobs_repo.SERVICES,
        MODES=jobs_repo.MODES,
        ON_FREE=jobs_repo.ON_FREE,
        STATUS_LABELS=jobs_repo.STATUS_LABELS,
    )

    def render(request: Request, name: str, status_code: int = 200, **context) -> Response:
        context.setdefault("flash", request.session.pop("flash", None))
        return templates.TemplateResponse(request, name, context, status_code=status_code)

    # --- dependencias -------------------------------------------------------

    def get_conn() -> Iterator[sqlite3.Connection]:
        conn = connect(settings.data_dir)
        try:
            yield conn
        finally:
            conn.close()

    Conn = Annotated[sqlite3.Connection, Depends(get_conn)]

    def require_house(request: Request, conn: Conn) -> sqlite3.Connection:
        if get_setting(conn, HOUSE_PASSWORD_KEY) is None:
            raise Redirect("/configurar")
        if not request.session.get("house"):
            raise Redirect("/entrar")
        return conn

    def require_profile(request: Request, conn: Annotated[sqlite3.Connection, Depends(require_house)]):
        profile_id = request.session.get("profile_id")
        profile = profiles_repo.get_profile(conn, profile_id) if profile_id else None
        if profile is None:
            request.session.pop("profile_id", None)
            raise Redirect("/perfiles")
        return profile

    HouseConn = Annotated[sqlite3.Connection, Depends(require_house)]
    CurrentProfile = Annotated[profiles_repo.Profile, Depends(require_profile)]

    @app.exception_handler(Redirect)
    async def handle_redirect(_request: Request, exc: Redirect) -> Response:
        return _redirect(exc.url)

    # --- PWA: manifest y service worker deben servirse desde la raíz ----------

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest() -> FileResponse:
        return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(STATIC_DIR / "icon-192.png", media_type="image/png")

    @app.get("/sw.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript")

    # --- cuenta de casa -----------------------------------------------------

    @app.get("/")
    def home(profile: CurrentProfile) -> Response:
        return _redirect("/reservas")

    @app.get("/configurar")
    def setup_form(request: Request, conn: Conn) -> Response:
        if get_setting(conn, HOUSE_PASSWORD_KEY) is not None:
            return _redirect("/entrar")
        return render(request, "setup.html", min_length=MIN_HOUSE_PASSWORD)

    @app.post("/configurar")
    def setup_submit(request: Request, conn: Conn, password: StrForm, password2: StrForm) -> Response:
        if get_setting(conn, HOUSE_PASSWORD_KEY) is not None:
            return _redirect("/entrar")
        error = None
        if len(password) < MIN_HOUSE_PASSWORD:
            error = f"Mínimo {MIN_HOUSE_PASSWORD} caracteres."
        elif password != password2:
            error = "Las contraseñas no coinciden."
        if error:
            return render(request, "setup.html", 400, error=error, min_length=MIN_HOUSE_PASSWORD)
        set_setting(conn, HOUSE_PASSWORD_KEY, hash_password(password))
        conn.commit()
        request.session["house"] = True
        return _redirect("/perfiles")

    @app.get("/entrar")
    def login_form(request: Request, conn: Conn) -> Response:
        if get_setting(conn, HOUSE_PASSWORD_KEY) is None:
            return _redirect("/configurar")
        if request.session.get("house"):
            return _redirect("/perfiles")
        return render(request, "login.html")

    @app.post("/entrar")
    def login_submit(request: Request, conn: Conn, password: StrForm) -> Response:
        stored = get_setting(conn, HOUSE_PASSWORD_KEY)
        if stored is None:
            return _redirect("/configurar")
        if not verify_password(password, stored):
            time.sleep(1)  # frena intentos seguidos
            return render(request, "login.html", 401, error="Contraseña incorrecta.")
        request.session.clear()
        request.session["house"] = True
        return _redirect("/perfiles")

    @app.post("/salir")
    def logout(request: Request) -> Response:
        request.session.clear()
        return _redirect("/entrar")

    # --- perfiles -----------------------------------------------------------

    @app.get("/perfiles")
    def profiles_page(request: Request, conn: HouseConn) -> Response:
        return render(
            request,
            "profiles.html",
            profiles=profiles_repo.list_profiles(conn),
            current_id=request.session.get("profile_id"),
        )

    @app.post("/perfiles/{profile_id}/elegir")
    def choose_profile(request: Request, conn: HouseConn, profile_id: int) -> Response:
        if profiles_repo.get_profile(conn, profile_id) is None:
            return _redirect("/perfiles")
        request.session["profile_id"] = profile_id
        return _redirect("/reservas")

    def _profile_form(
        request: Request, conn: sqlite3.Connection, status_code=200, error: str | None = None, **values
    ) -> Response:
        used = {p.color for p in profiles_repo.list_profiles(conn)}
        default_color = next((c for c in profiles_repo.COLORS if c not in used), profiles_repo.COLORS[0])
        values.setdefault("color", default_color)
        return render(
            request,
            "profile_new.html",
            status_code,
            colors=profiles_repo.COLORS,
            key_missing=settings.fernet_key is None,
            error=error,
            values=values,
        )

    @app.get("/perfiles/nuevo")
    def new_profile_form(request: Request, conn: HouseConn) -> Response:
        return _profile_form(request, conn)

    @app.post("/perfiles/nuevo")
    def new_profile_submit(
        request: Request,
        conn: HouseConn,
        display_name: StrForm,
        color: StrForm,
        portal_username: StrForm,
        portal_password: StrForm,
    ) -> Response:
        display_name = display_name.strip()
        portal_username = portal_username.strip()
        # La contraseña nunca se devuelve al formulario
        values = {"display_name": display_name, "color": color, "portal_username": portal_username}

        error = None
        if not display_name or len(display_name) > 20:
            error = "El nombre debe tener entre 1 y 20 caracteres."
        elif color not in profiles_repo.COLORS:
            error = "Elige un color de la lista."
        elif not portal_username or not portal_password:
            error = "Faltan el usuario o la contraseña del portal."
        elif profiles_repo.name_exists(conn, display_name):
            error = "Ya hay un perfil con ese nombre."
        if error:
            return _profile_form(request, conn, 400, error=error, **values)

        try:
            password_enc = crypto.encrypt(settings, portal_password)
        except crypto.MissingKeyError:
            return _profile_form(
                request, conn, 500, error="Falta FERNET_KEY en el servidor: no se puede guardar.", **values
            )
        profile_id = profiles_repo.create_profile(conn, display_name, color, portal_username, password_enc)
        conn.commit()
        request.session["profile_id"] = profile_id
        request.session["flash"] = f"Perfil «{display_name}» creado."
        return _redirect("/reservas")

    # --- reservar -----------------------------------------------------------

    def _book_form(request, conn, profile, status_code=200, error: str | None = None, **values) -> Response:
        tomorrow = datetime.now(settings.zone).date() + timedelta(days=1)
        values.setdefault("slot_date", tomorrow.isoformat())
        values.setdefault("service", "multitrabajo")
        values.setdefault("mode", "reservar")
        values.setdefault("on_free", "reservar")
        values.setdefault("dry_run", True)
        return render(
            request,
            "book.html",
            status_code,
            profile=profile,
            nav="reservar",
            centers=jobs_repo.recent_centers(conn, profile.id),
            forced_dry_run=settings.dry_run,
            error=error,
            values=values,
        )

    @app.get("/reservar")
    def book_form(request: Request, conn: HouseConn, profile: CurrentProfile) -> Response:
        return _book_form(request, conn, profile)

    @app.post("/reservar")
    def book_submit(
        request: Request,
        conn: HouseConn,
        profile: CurrentProfile,
        center: StrForm,
        service: StrForm,
        slot_date: StrForm,
        slot_time: StrForm,
        mode: StrForm,
        on_free: Annotated[str, Form()] = "reservar",
        dry_run: Annotated[bool, Form()] = False,
    ) -> Response:
        center = center.strip()
        values = {
            "center": center,
            "service": service,
            "slot_date": slot_date,
            "slot_time": slot_time,
            "mode": mode,
            "on_free": on_free,
            "dry_run": dry_run,
        }

        slot_at = None
        error = None
        try:
            local = datetime.combine(date.fromisoformat(slot_date), dtime.fromisoformat(slot_time))
            slot_at = local.replace(tzinfo=settings.zone)
        except ValueError:
            error = "Fecha u hora no válidas."
        if error is None:
            if not center or len(center) > 80:
                error = "Indica el polideportivo."
            elif service not in jobs_repo.SERVICES:
                error = "Elige un servicio."
            elif mode not in jobs_repo.MODES:
                error = "Elige reservar u observar."
            elif on_free not in jobs_repo.ON_FREE:
                error = "Elige qué hacer si se libera una plaza."
            elif slot_at <= datetime.now(settings.zone):
                error = "Ese turno ya ha pasado."
        if error:
            return _book_form(request, conn, profile, 400, error=error, **values)

        jobs_repo.create_job(
            conn,
            profile_id=profile.id,
            center=center,
            service=service,
            slot_at=slot_at,
            mode=mode,
            on_free=on_free,
            dry_run=dry_run or settings.dry_run,
        )
        conn.commit()
        request.session["flash"] = "Reserva programada."
        return _redirect("/reservas")

    # --- mis reservas -------------------------------------------------------

    @app.get("/reservas")
    def jobs_page(request: Request, conn: HouseConn, profile: CurrentProfile) -> Response:
        return render(
            request,
            "jobs.html",
            profile=profile,
            nav="reservas",
            jobs=jobs_repo.list_jobs(conn, profile.id),
        )

    @app.post("/reservas/{job_id}/cancelar")
    def cancel_job(request: Request, conn: HouseConn, profile: CurrentProfile, job_id: int) -> Response:
        if jobs_repo.cancel_job(conn, job_id, profile.id):
            conn.commit()
            request.session["flash"] = "Programación cancelada."
        return _redirect("/reservas")

    return app
