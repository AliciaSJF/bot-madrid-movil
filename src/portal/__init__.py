"""Todo lo que habla con deportesweb.madrid.es. El resto del código solo usa lo exportado aquí."""

from src.portal.auth import SessionResult, ensure_session
from src.portal.cancel import CancelResult, cancel_reservation
from src.portal.centers import PortalCenter, fetch_centers
from src.portal.errors import (
    BrowserClosed,
    CaptchaDetected,
    LoginRejected,
    PortalError,
    SessionExpired,
    VerificationRequired,
)
from src.portal.slots import PortalSlot, fetch_slots, fetch_slots_many

__all__ = [
    "BrowserClosed",
    "CancelResult",
    "CaptchaDetected",
    "LoginRejected",
    "PortalCenter",
    "PortalError",
    "PortalSlot",
    "SessionExpired",
    "SessionResult",
    "VerificationRequired",
    "cancel_reservation",
    "ensure_session",
    "fetch_centers",
    "fetch_slots",
    "fetch_slots_many",
]
