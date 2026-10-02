"""Precarga en segundo plano al elegir un perfil, para que "Reservar" abra al instante.

Qué trae (en una sola visita al portal por paso, y solo si hace falta):
    1. La lista de polideportivos de cada servicio, si no hay o tiene más de un día.
    2. Los turnos de la semana de los favoritos del perfil, si la foto tiene más de STALE_AFTER.

Un único hilo de trabajo y como mucho una precarga pendiente por perfil. El navegador ya es de
uno en uno (src/portal/browser.py): si una página pide datos mientras tanto, espera y los reutiliza.
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from src import portal
from src.config import Settings
from src.core import slots as core_slots
from src.core.booking import booking_imminent
from src.core.centers import refresh_centers
from src.core.connection import with_session
from src.db import centers as centers_repo
from src.db import slots as slots_repo
from src.db.database import connect

log = logging.getLogger(__name__)

SERVICES = ("multitrabajo", "piscina")
CENTERS_MAX_AGE = timedelta(days=1)

# Los tests lo apagan (tests/conftest.py): nunca deben hablar con el portal real
ENABLED = True

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="precarga")
_pending: set[int] = set()
_pending_lock = threading.Lock()


def schedule_prefetch(settings: Settings, profile_id: int) -> bool:
    """Encola la precarga del perfil. Devuelve False si ya había una pendiente o está apagada."""
    if not ENABLED:
        return False
    with _pending_lock:
        if profile_id in _pending:
            return False
        _pending.add(profile_id)
    _executor.submit(_run, settings, profile_id)
    return True


def _run(settings: Settings, profile_id: int) -> None:
    try:
        prefetch(settings, profile_id, datetime.now(settings.zone))
    except portal.PortalError as exc:
        log.warning("Precarga del perfil %s sin terminar: %s", profile_id, exc)
    except Exception:  # un fallo en segundo plano no debe tumbar nada; queda en el log
        log.exception("Error inesperado en la precarga del perfil %s", profile_id)
    finally:
        with _pending_lock:
            _pending.discard(profile_id)


def prefetch(settings: Settings, profile_id: int, now: datetime) -> None:
    conn = connect(settings.data_dir)
    try:
        if booking_imminent(conn, settings, now):
            return  # el navegador es para la reserva que está a punto de abrir
        for service in SERVICES:
            updated = centers_repo.last_updated(conn, service)
            if updated is None or datetime.now(UTC) - updated > CENTERS_MAX_AGE:
                refresh_centers(conn, settings, profile_id, service)

        days = core_slots.week(now.date())
        targets = [
            (service, center.portal_id)
            for service in SERVICES
            for center in centers_repo.list_centers(conn, service, profile_id)
            if center.is_favorite and core_slots.needs_refresh(conn, service, center.portal_id, days, now)
        ]
        if not targets:
            return

        results = with_session(
            conn, settings, profile_id, lambda: portal.fetch_slots_many(settings, profile_id, targets, days)
        )
        for (service, center_id), found in results.items():
            if isinstance(found, portal.PortalError):
                log.warning("Precarga: sin turnos de %s/%s: %s", service, center_id, found)
                continue
            slots_repo.replace_days(
                conn,
                service,
                center_id,
                days,
                (slots_repo.StoredSlot(s.day, s.time, s.activity, s.free, s.total, s.selectable) for s in found),
                now,
            )
        conn.commit()
    finally:
        conn.close()
