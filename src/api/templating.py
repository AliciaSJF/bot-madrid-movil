"""Plantillas Jinja: filtros, variables globales y render() con el mensaje flash."""

from datetime import date, datetime
from pathlib import Path

from fastapi import Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from src.db import jobs as jobs_repo

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

WEEKDAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]

templates = Jinja2Templates(directory=BASE_DIR / "templates")


@pass_context
def format_slot(context, dt: datetime) -> str:
    """UTC de la BD → «jue 1 oct · 19:00» en la zona de la app."""
    zone = context["request"].app.state.settings.zone
    local = dt.astimezone(zone)
    return f"{WEEKDAYS[local.weekday()]} {local.day} {MONTHS[local.month - 1]} · {local:%H:%M}"


def weekday_short(d: date) -> str:
    return WEEKDAYS[d.weekday()]


def day_month(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


def activity_label(activity: str) -> str:
    """«Nado libre · Calle central» → «Calle central»; si no hay subtítulo, el nombre tal cual."""
    return activity.split(" · ", 1)[-1]


templates.env.filters["slot"] = format_slot
templates.env.filters["activity"] = activity_label
templates.env.filters["weekday"] = weekday_short
templates.env.filters["day_month"] = day_month
templates.env.globals.update(
    SERVICES=jobs_repo.SERVICES,
    MODES=jobs_repo.MODES,
    ON_FREE=jobs_repo.ON_FREE,
    STATUS_LABELS=jobs_repo.STATUS_LABELS,
)


def flash(request: Request, message: str) -> None:
    """Mensaje que se muestra una sola vez en la siguiente pantalla."""
    request.session["flash"] = message


def render(request: Request, name: str, status_code: int = 200, **context) -> Response:
    context.setdefault("flash", request.session.pop("flash", None))
    return templates.TemplateResponse(request, name, context, status_code=status_code)
