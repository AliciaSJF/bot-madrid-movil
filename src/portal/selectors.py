"""URLs y selectores del portal. Si el portal cambia, se toca aquí.

Cada selector indica si está verificado y cuándo. No añadir selectores inventados:
pedir captura o HTML del paso del portal antes.
"""

BASE_URL = "https://deportesweb.madrid.es"
LOGIN_URL = f"{BASE_URL}/DeportesWeb/Login"
HOME_URL = f"{BASE_URL}/DeportesWeb/Home"
HOME_PATH = "/DeportesWeb/Home"
LOGIN_PATH = "/DeportesWeb/Login"

# --- Login: verificado el 2026-09-30 en la página pública (sin enviar el formulario) ---

# La página muestra tarjetas: "Correo y contraseña", "Sede electrónica", "No identificado".
# El formulario aparece al pulsar la primera.
LOGIN_OPTION_EMAIL = "article.navigation-section-widget-collection-item:has-text('Correo y contraseña')"
USERNAME_INPUT = "#ContentFixedSection_uLogin_txtIdentificador"
PASSWORD_INPUT = "#ContentFixedSection_uLogin_txtContrasena"
KEEP_SESSION_CHECKBOX = "#ContentFixedSection_uLogin_chkNoCerrarSesion"
LOGIN_BUTTON = "#ContentFixedSection_uLogin_btnLogin"

# Paneles de verificación: existen vacíos en la página; si se llenan, el portal pide algo más
LOGIN_VERIFICATION_PANELS = [
    "#ContentFixedSection_uLogin_uLoginVerification_uplContenedor",
    "#ContentFixedSection_uLoginVerification_uplContenedor",
]

# Del contexto del proyecto (respuesta del POST de login); pendiente de ver en pantalla
LOGIN_ERROR_TEXT = "Intento de inicio de sesión no válido"

# --- Estado de la sesión ---

# Verificado 2026-09-30: en Home como invitada hay un enlace con este postback.
# SIN VERIFICAR: que no aparezca con la sesión iniciada.
GUEST_LOGIN_LINK = "a[href*='IniciarSesion']"

# --- Bloqueos que el bot no debe intentar saltar ---

CAPTCHA = "iframe[src*='recaptcha'], iframe[src*='hcaptcha'], [id*='captcha' i], [class*='captcha' i]"
