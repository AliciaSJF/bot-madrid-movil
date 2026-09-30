"""Todo lo que habla con deportesweb.madrid.es. El resto del código solo usa lo exportado aquí."""

from src.portal.auth import SessionResult, ensure_session
from src.portal.errors import CaptchaDetected, LoginRejected, PortalError, VerificationRequired

__all__ = [
    "CaptchaDetected",
    "LoginRejected",
    "PortalError",
    "SessionResult",
    "VerificationRequired",
    "ensure_session",
]
