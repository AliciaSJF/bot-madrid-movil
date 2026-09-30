"""Errores del portal. Ninguno lleva contraseñas, cookies ni tokens en el mensaje."""


class PortalError(Exception):
    """Fallo genérico hablando con el portal (red, tiempo agotado, página inesperada)."""


class LoginRejected(PortalError):
    """Usuario o contraseña incorrectos. No reintentar: se podría bloquear la cuenta."""


class VerificationRequired(PortalError):
    """El portal pide una verificación adicional. Hay que hacerla a mano."""


class CaptchaDetected(PortalError):
    """Hay un captcha. El bot no lo intenta resolver: para y avisa."""


class SessionExpired(PortalError):
    """La sesión guardada ya no vale: hay que volver a iniciar sesión."""
