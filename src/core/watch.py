"""Modo observar: vigilar turnos completos por si se libera una plaza.

Cada pasada (la lanza el programador cada WATCH_INTERVAL):
    - como mucho settings.max_active_watches vigilancias a la vez (las más antiguas primero)
    - una sola consulta por centro y servicio, aunque lo vigilen varios perfiles (y como mucho una por
      minuto: la foto de turnos es compartida, ver src/core/slots.py)
    - si un turno tiene plaza: «reservar» → lanza la reserva ya; «avisar» → aviso con botón «Reservar ahora»
    - al empezar el turno, la vigilancia termina (expirado)
    - no se consulta nada si hay una apertura a punto de llegar (el navegador es para reservar)
"""

import logging
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta

from src import portal
from src.config import Settings
from src.core import slots as core_slots
from src.core.booking import booking_imminent, slot_label
from src.db import jobs as jobs_repo
from src.db import profiles as profiles_repo
from src.db import slots as slots_repo
from src.notify import notify

log = logging.getLogger(__name__)

WATCHING = ("pendiente", "vigilando")


def watch_interval(settings: Settings) -> timedelta:
    # Nunca por debajo de 30 s (config) ni de lo que tarda la foto en poder refrescarse (60 s)
    return max(timedelta(seconds=settings.watch_min_interval_s), core_slots.MIN_REFRESH_INTERVAL)


def watch_tick(
    conn: sqlite3.Connection, settings: Settings, now: datetime, start_booking: Callable[[int], None]
) -> None:
    watches = [j for j in jobs_repo.list_active(conn) if j.mode == "observar" and j.status in WATCHING]
    watches.sort(key=lambda j: j.id)
    watches = watches[: settings.max_active_watches]
    if not watches or booking_imminent(conn, settings, now):
        return

    for job in watches:
        if job.status == "pendiente":
            jobs_repo.set_status(conn, job.id, "vigilando", "Vigilando por si se libera una plaza.")
    conn.commit()

    # Una consulta por (servicio, centro), con la sesión de un perfil que esté conectado
    groups: dict[tuple[str, int], list[jobs_repo.Job]] = {}
    for job in watches:
        groups.setdefault((job.service, job.center_id), []).append(job)
    for (service, center_id), group in groups.items():
        days = sorted({j.slot_at.astimezone(settings.zone).date() for j in group})
        profile_id = next(
            (j.profile_id for j in group if profiles_repo.get_profile(conn, j.profile_id).portal_status == "ok"),
            None,
        )
        if profile_id is None:
            continue
        try:
            core_slots.refresh_slots(conn, settings, profile_id, service, center_id, days, now)
        except portal.PortalError as exc:
            log.warning("Vigilancia de %s/%s sin datos: %s", service, center_id, exc)
            continue

        for job in group:
            start = job.slot_at.astimezone(settings.zone)
            stored = slots_repo.get_slot(conn, service, center_id, start.date(), start.time(), job.activity)
            if stored is None:
                continue
            if core_slots.classify(stored, now, settings.zone).status != "libre":
                continue
            _on_free(conn, settings, job, stored.free, start_booking)


def _on_free(conn, settings, job: jobs_repo.Job, free: int, start_booking: Callable[[int], None]) -> None:
    label = slot_label(settings, job)
    jobs_repo.add_attempt(conn, job.id, "vigilar", "plaza", f"{free} plaza(s) libre(s)")
    if job.on_free == "avisar":
        jobs_repo.set_status(conn, job.id, "avisado", f"Se liberó una plaza ({free} libre/s).")
        conn.commit()
        notify(conn, settings, job.profile_id, f"👀 Se ha liberado una plaza\n{label}",
               [("Reservar ahora", f"res:{job.id}"), ("Ignorar", f"ign:{job.id}")])
        return
    jobs_repo.set_status(conn, job.id, "plaza_liberada", f"Se liberó una plaza ({free} libre/s): reservando.")
    conn.commit()
    start_booking(job.id)


def expire_past(conn: sqlite3.Connection, settings: Settings, now: datetime) -> None:
    """Termina lo que ya no tiene sentido: turnos que ya han empezado."""
    for job in jobs_repo.list_active(conn):
        if job.status == "reservando" or job.slot_at.astimezone(settings.zone) > now:
            continue
        if jobs_repo.set_status(conn, job.id, "expirado", "El turno ya ha empezado.", only_if=jobs_repo.ACTIVE_STATUSES):
            conn.commit()
            if job.mode == "observar":
                notify(conn, settings, job.profile_id,
                       f"⌛ Fin de la vigilancia: no se liberó ninguna plaza\n{slot_label(settings, job)}")
