"""URLs y selectores del portal. Si el portal cambia, se toca aquí.

Cada selector indica si está verificado y cuándo. No añadir selectores inventados:
pedir captura o HTML del paso del portal antes.
"""

import re

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

# --- Home con sesión: verificado 2026-09-30 ---

# Tarjetas de la sección "Entradas de uso libre" (article con h4[title]); navegan por JS, sin href
HOME_CARD = "article.navigation-section-widget-collection-item:has(h4[title='{title}'])"
SERVICE_CARD_TITLES = {
    "multitrabajo": "Sala multitrabajo",
    "piscina": "Nado libre en piscina cubierta",
}

# --- Lista de centros de un servicio (AltaEventos?token=...): verificado 2026-09-30 ---

# Sala multitrabajo: 43 centros; nado libre: 36. El mismo centro tiene el mismo id en ambas.
CENTERS_CONTAINER = "#ContentFixedSection_uAltaEventos_uCentrosSeleccionar_divCentros"
CENTER_CARD = f"{CENTERS_CONTAINER} article.navigation-section-widget-collection-item"
# Dentro de cada tarjeta: h4[title] = nombre; span#..._spnFavorite_<id> con texto
# "favorite" / "favorite_border"; div.navigation-section-widget-collection-item-description[title] = dirección.
# <id> es el facility_code que usa el portal (p. ej. al marcar favorito).
EXTRACT_CENTERS_JS = """
cards => cards.map(card => {
  const fav = card.querySelector("span[id*='_spnFavorite_']");
  const desc = card.querySelector('.navigation-section-widget-collection-item-description');
  return {
    id: fav ? parseInt(fav.id.split('_').pop(), 10) : null,
    name: card.querySelector('h4')?.getAttribute('title')?.trim() || '',
    address: desc?.getAttribute('title')?.trim() || '',
    favorite: fav ? fav.textContent.trim() === 'favorite' : false,
  };
})
"""

# --- Turnos de un centro (tras pulsar el centro en la lista): verificado 2026-09-30 ---

# Tarjeta de un centro por su facility_code (se pulsa el título, no el corazón)
CENTER_CARD_BY_ID = CENTER_CARD + ":has(span[id$='_spnFavorite_{portal_id}']) h4"
# Calendario visible (hay otro en una ventana modal de "Elegir disponibilidad"; ese no)
SLOTS_DATEPICKER = "#ContentFixedSection_uAltaEventos_uAltaEventosFechas_datetimepicker"
DATEPICKER_DAY = SLOTS_DATEPICKER + " td[data-day='{day}']"  # day = dd/mm/aaaa
DATEPICKER_ACTIVE_DAY = SLOTS_DATEPICKER + " td.active"
DATEPICKER_NEXT = SLOTS_DATEPICKER + " .datepicker-days th.next"
SLOT_LISTS = "ul.media-list"
# Cada actividad: div.panel-body con un div.media (h4.media-heading = nombre, p = subtítulo/edad,
# duración, descripción) seguido de su ul.media-list. Un centro puede tener varias actividades
# con el mismo nombre: en piscina, una por calle ("Nado libre" + "Calle central" / "Calle CON
# BORDILLO" / "Calle SIN BORDILLO"). En sala el primer <p> es la edad ("A partir de 18 años").
# Cada turno es li.media con h4 = hora, <strike> si no se puede elegir, y dos span: plazas
# libres y "/aforo". Los turnos que aún no abren salen con libres = aforo.
EXTRACT_SLOTS_JS = """
lists => lists.map(ul => {
  const info = ul.previousElementSibling;
  const title = (info?.querySelector('.media-heading') || ul.parentElement.querySelector('.media-heading'))
    ?.textContent.trim() || '';
  const first = info?.querySelector('p')?.textContent.trim() || '';
  const subtitle = /^a partir/i.test(first) ? '' : first;
  return {
    activity: subtitle ? `${title} · ${subtitle}` : title,
    slots: [...ul.querySelectorAll('li.media')].map(li => {
      const spans = li.querySelectorAll('span');
      return {
        time: li.querySelector('h4')?.textContent.trim() || '',
        struck: !!li.querySelector('strike'),
        free: parseInt(spans[0]?.textContent || '', 10),
        total: parseInt((spans[1]?.textContent || '').replace('/', ''), 10),
      };
    }),
  };
})
"""

# --- Carrito y pago: verificado 2026-09-30 (sin pulsar "Confirmar la compra") ---

# Pulsar la hora de un turno elegible lo mete en el carrito y lleva aquí
CART_PATH = "/DeportesWeb/Modulos/VentaServicios/CarritoConfirmar"
# Enlace al carrito en la cabecera (verificado 2026-09-30): pulsarlo sí funciona, abrir la URL a mano no
HEADER_CART_LINK = "#aCarrito"
# Mensaje al pulsar un turno que ya está en tu carrito o ya reservaste (verificado 2026-09-30):
# «La sesión seleccionada no permite más de 1 reserva(s) por persona.»
ALREADY_BOOKED_TEXT = "no permite más de"
# Abrir el carrito por URL no sirve: el portal devuelve a Home ("el estado de la página ha variado")
CART_ITEMS = "ul.list-group.cart > li.list-group-item:not([id$='_liTotal'])"
CART_TOTAL = "[id$='uCarritoConfirmar_liTotal']"
PAYMENT_METHODS = "#ContentFixedSection_uCarritoConfirmar_divPaymentMethods li.list-group-item"
PAYMENT_WALLET_TEXT = "Monedero"  # el orden en pantalla es Tarjeta bancaria, Bizum, Monedero; ninguno marcado
CART_TERMS = "#ContentFixedSection_uCarritoConfirmar_divTerms"  # vacío al verlo; si trae casillas, no seguir
CART_CONFIRM_BUTTON = "#ContentFixedSection_uCarritoConfirmar_btnConfirmCart"
PORTAL_HOST = "deportesweb.madrid.es"
# Avisos de los UpdatePanel (p. ej. uCentrosSeleccionar_uAlert_showDanger)
VISIBLE_ALERTS = "[id*='uAlert']:visible, .alert:visible"

# --- Mis reservas y anulación: verificado 2026-09-30 con una anulación real ---

ACCOUNT_LINK = "a[href*='MiCuenta']"  # el correo de la cabecera (postback «MiCuenta»)
ACCOUNT_PATH = "/DeportesWeb/Account"
ACCOUNT_CARD = "article:has(h4[title='{title}'])"
ACCOUNT_FREE_USE_CARD = "Entradas de uso libre"
ACCOUNT_WALLET_CARD = "Movimientos monedero"
# Tabla de reservas (10 por página). Columnas visibles: estado («Pendiente», «Anulado», «Asistido»
# o vacío), fecha dd/mm/aaaa, día, hora inicio, hora fin, asistencia, actividad, sala, centro, apellidos,
# nombre, importe. El centro puede venir sin tildes («Juan de Dios Roman»).
RESERVATION_ROWS = "table tbody tr"
RESERVATION_CONSULT = "button[title='Consultar']"  # clase hidden-lg: se pulsa por JS
# SIN VERIFICAR: la paginación («« ‹ 1 2 3 › »») se vio en el texto, no se ha pulsado
TABLE_NEXT_PAGE = "ul.pagination li:not(.disabled) a:text-is('›')"
CANCEL_BUTTON = "#ContentSection_uReservas_uCarritosFicha_btnRefundCart"  # «Anular» en la ficha
CANCEL_CONFIRM_YES = "#ContentSection_uReservas_uCarritosFicha_uWarningModal_btnYes:visible"  # hay dos con ese id
CANCELLED_TEXT = "Anulado"
WALLET_BALANCE = re.compile(r"Saldo actual\s+(\d{1,4}(?:\.\d{3})*,\d{2})\s*€")
