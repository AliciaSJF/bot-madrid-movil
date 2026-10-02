# Bot de reservas – Deportes Madrid (uso libre)

Contexto para Claude Code. Léelo entero antes de tocar código. Si algo de aquí choca con lo que veas en el repo o en el portal, para y pregunta en vez de asumir.

## 0. Estado actual

| Hito | Estado |
|---|---|
| 1. Repo, entorno, `.gitignore`, estructura | ✅ hecho |
| 2. Front básico: cuenta de casa, perfiles, reservar, mis reservas (sin portal) | ✅ hecho (rama `feat/front-perfiles`) |
| 3. Login por CLI con Playwright | ✅ probado con cuenta real |
| 4a. Lista de polideportivos por servicio + favoritos | ✅ probado con cuenta real |
| 4b. Turnos de la semana con plazas libres | ✅ probado con cuenta real |
| 5–8. Reserva en la apertura, reintentos, monedero, vigilancia y avisos con botón «Observar» | ✅ compra real verificada el 30/09 (y anulada); falta probar una apertura real con compra |
| 5–11 | pendiente |

## 1. Qué es y para qué

Aplicación personal que reserva entradas de uso libre en polideportivos municipales de Madrid a través del portal web (`https://deportesweb.madrid.es/DeportesWeb/Login`), sin usar la app Madrid Móvil.

Servicios que interesan ahora mismo:

- Sala multitrabajo / musculación
- Nado libre en piscina cubierta

Corre en una Raspberry Pi de casa, dentro de un contenedor Docker, y se controla desde una interfaz web pensada primero para el móvil.

Es un proyecto de uso doméstico para 2 usuarios (la dueña del proyecto y su pareja). No es un servicio público ni multiusuario abierto.

## 2. Prioridad de diseño: MÓVIL PRIMERO

La mayoría de las veces se usará desde el móvil; el ordenador es secundario.

- Diseñar y probar primero a ~375 px de ancho; el escritorio es una adaptación, no al revés.
- Una sola columna, navegación abajo o menú simple, botones y controles táctiles de al menos 44 px.
- Formularios con controles nativos del móvil: `<input type="date">`, `<input type="time">`, `<select>`. Nada de widgets pesados de calendario.
- Acciones frecuentes en pocos toques: crear una reserva programada debe poder hacerse en menos de ~30 segundos desde el móvil.
- Que se pueda instalar como PWA (manifest + icono) para añadirla a la pantalla de inicio.
- Sin frameworks JS pesados: FastAPI + plantillas Jinja + HTMX, CSS ligero (propio o una librería pequeña, servida localmente).
- Al elegir un turno que aparece completo, mostrar de forma destacada el botón "Observar" (avisarme o reservar si se libera), con las otras opciones como secundarias.
- Estados y errores legibles en pantalla pequeña (badges de estado, mensajes cortos, sin tablas anchas; usar tarjetas).
- Las alertas importantes (reserva conseguida, fallo, plaza liberada) van por Telegram, para no depender de que la web esté abierta.
- Acceso desde fuera de casa: VPN tipo Tailscale. No exponer la web a internet.

## 3. Requisitos funcionales

1. Usuarios, estilo Netflix:
   - **Cuenta de casa**: una contraseña común para entrar en la web, pedida una vez por dispositivo (sesión larga). Se crea la primera vez que se abre la web.
   - **Perfiles**: tras entrar, pantalla con tarjetas (inicial + color) para elegir quién eres, y "+ Añadir perfil".
   - Crear un perfil pide nombre visible, color, y usuario y contraseña del portal de la Comunidad de Madrid (la contraseña se guarda cifrada).
   - Cada perfil tiene sus credenciales, su sesión de Playwright, su chat de Telegram y sus reservas. Se cambia de perfil desde la barra inferior sin cerrar sesión.
   - Sin PIN por perfil de momento (se puede añadir después).
   - Se empieza con un solo perfil; el segundo se añade desde la web sin tocar código.
2. Crear un job eligiendo:
   - usuario
   - polideportivo
   - tipo de servicio: sala multitrabajo o piscina (nado libre)
   - fecha y hora del turno
   - modo: `reservar` u `observar` (ver punto 3)
3. Dos modos independientes. Observar es una opción por derecho propio, no solo un plan B de reservar:
   - `reservar`: programa el intento de reserva para el momento de apertura del turno (ver punto 4). Si el turno está completo cuando se abre, aplica su estrategia de respaldo: (a) horas alternativas (lista ordenada de otras horas del mismo día) y/o (b) pasar a observar ese mismo turno.
   - `observar`: vigila si se libera una plaza en el turno elegido. No intenta reservar en la apertura; empieza directamente a vigilar. Es la opción para turnos que ya están completos ahora mismo.
   - Al crear un job, si el turno consultado aparece completo, la interfaz debe ofrecer Observar como primera opción (botón principal), por delante de "reservar en la apertura" o "probar otra hora".
   - En modo observar se elige la acción al liberarse: `reservar automáticamente` o `solo avisarme` (por Telegram). Por defecto, reservar automáticamente.
   - Se elige también una hora límite de vigilancia (por defecto, el inicio del turno). Al conseguir plaza, o al llegar la hora límite, el job termina.
   - Se pueden combinar: observar varios turnos alternativos a la vez (p. ej. 19:00 y 20:00); el primero que se libere gana y el resto se cancelan.
   - Deduplicar consultas: si dos jobs (de usuarios distintos) observan el mismo centro/servicio/turno, se hace una sola consulta al portal y su resultado sirve a ambos.
4. Lanzar la reserva en el momento de apertura del turno (solo modo `reservar`; ver reglas del portal), con la sesión ya iniciada y la página lista.
5. Ver y gestionar las reservas programadas: estado, historial de intentos, cancelar una programación.
6. Notificaciones por Telegram con el resultado.
7. ~~Modo prueba (`dry_run`)~~: quitado el 02/10 a petición de la usuaria (confundía). Sirvió para verificar las aperturas el 30/09.

## 4. Requisitos no funcionales

- **Seguridad**
  - Credenciales del portal cifradas en reposo (por ejemplo `cryptography.Fernet`), con la clave en variable de entorno, nunca en la base de datos ni en el repo.
  - La web tiene login propio aunque solo sea accesible por red privada.
  - No guardar datos de tarjeta ni pagos. Si el flujo pide pagar, se usa saldo del monedero virtual o abono; el bot no toca tarjetas.
  - No registrar contraseñas, cookies ni tokens en logs.
- **Respeto al portal**
  - Límite global de peticiones y de reintentos. Intervalo mínimo de vigilancia: 30 s.
  - Máximo de jobs de vigilancia activos a la vez (configurable, valor bajo por defecto).
  - Respetar el máximo de sesiones por día. Nunca bucles de reservar/anular.
  - No intentar saltar captchas ni verificaciones. Si aparecen, parar y avisar.
- **Raspberry Pi**: imagen arm64, poca RAM. Lanzar el navegador solo cuando haga falta y cerrarlo al terminar; reutilizar la sesión guardada.
- **Zona horaria**: `Europe/Madrid` para todo lo visible. Guardar instantes en UTC en la base de datos. Cuidado con el cambio de hora.
- **Reloj**: NTP activo; el momento crítico depende de la hora exacta.

## 5. El portal (lo que sabemos)

Esto es lo observado hasta ahora. Se irá ampliando; marca como **verificado** lo que compruebes.

### Tecnología

- ASP.NET WebForms con UpdatePanels. Cada acción es un postback (`POST`) que envía campos ocultos (`__VIEWSTATE`, `__VIEWSTATEGENERATOR`, `__EVENTVALIDATION`) que el servidor regenera en cada respuesta.
- Las respuestas parciales usan el formato de UpdatePanel (segmentos separados por `|`).

### Login (`POST /DeportesWeb/Login`)

- Devuelve 200 OK tanto si funciona como si falla; no usar el código de estado para detectar éxito.
- Éxito: la respuesta contiene un `pageRedirect` a `/DeportesWeb/Home`.
- Fallo: la respuesta contiene el aviso "Intento de inicio de sesión no válido" y un ViewState nuevo.
- Botón de login: `#ContentFixedSection_uLogin_btnLogin`.
- **Verificado 2026-09-30 (página pública, sin enviar el formulario):**
  - La página de login muestra tres tarjetas (`article.navigation-section-widget-collection-item`): "Correo y contraseña", "Sede electrónica" y "No identificado". El formulario aparece al pulsar la primera.
  - Campos: `#ContentFixedSection_uLogin_txtIdentificador` (correo), `#ContentFixedSection_uLogin_txtContrasena`, casilla "No cerrar sesión" `#ContentFixedSection_uLogin_chkNoCerrarSesion` (el bot la marca).
  - No hay captcha visible. Los paneles `…uLoginVerification_uplContenedor` existen vacíos; si se llenan, el bot lo trata como verificación pedida.
  - Hay un banner de cookies; el bot no lo acepta.
  - "No identificado" entra en `/DeportesWeb/Home` como invitada, pero sin opciones de uso libre, sala ni piscina: **los polideportivos por servicio solo se ven con sesión**.
  - En Home como invitada aparece un enlace `__doPostBack(... 'IniciarSesion' ...)`. Se usa para detectar sesión caducada (sin verificar con sesión real).
- Selectores centralizados en `src/portal/selectors.py`, cada uno marcado como verificado o no.
- Existe un panel de verificación de login (`uLoginVerification`); no sabemos cuándo se activa. Vigilarlo, sobre todo al entrar desde un dispositivo nuevo (la Pi).
- Tras un login fallido no reintentar en bucle: parar y avisar, para no bloquear la cuenta.

### Home con sesión (verificado 2026-09-30)

- La sesión guardada (`storage_state`) sigue valiendo entre ejecuciones; con sesión no aparece el enlace `IniciarSesion`.
- Sección "Entradas de uso libre" con tarjetas `article` + `h4[title]` que navegan por JavaScript (sin `href`): "Sala multitrabajo", "Nado libre en piscina cubierta", "Oferta de entradas por uso y centro", "Vaso de enseñanza", etc.

### Lista de polideportivos de un servicio (verificado 2026-09-30)

- Ambos servicios llevan a `/DeportesWeb/Modulos/VentaServicios/Eventos/AltaEventos?token=...`. El `token` cambia en cada navegación: no guardarlo, llegar pulsando la tarjeta.
- Los centros cargan después de `networkidle` (postback parcial): esperar a `#ContentFixedSection_uAltaEventos_uCentrosSeleccionar_divCentros article`.
- Cada tarjeta: `h4[title]` = nombre, `.navigation-section-widget-collection-item-description[title]` = dirección, `span#…_spnFavorite_<id>` con texto `favorite` / `favorite_border`.
- `<id>` es el `facility_code` del centro y es el mismo en todos los servicios. Sala multitrabajo: 43 centros; nado libre: 36 (13 solo sala, 6 solo piscina).
- **Favoritos del portal = de la cuenta** (no del servicio; son los mismos que en Madrid Móvil). Se ponen/quitan con postbacks `SetFavorite` / `UnsetFavorite` con `facility_code`. La app **no** escribe favoritos en el portal: los lee al actualizar y los suma a los del perfil.
- Hay un interruptor "Mostrar centros con disponibilidad" (con fecha) y un buscador.
- Elegir un centro lanza el postback `SelectFacility` (`menu_code`, fecha opcional).

### Turnos de un centro (verificado 2026-09-30)

- Tras pulsar el centro: cabecera con nombre/dirección, calendario mensual `#ContentFixedSection_uAltaEventos_uAltaEventosFechas_datetimepicker` (celdas `td[data-day='dd/mm/aaaa']`, hoy con clase `active`) y, por actividad, un bloque con `h4.media-heading` (p. ej. "SALA MUSCULACION", "Nado libre"), la duración (sala: 90´) y `ul.media-list` de turnos.
- Cada turno: `li.media` con `h4` = hora y dos `span`: **plazas libres** y `/aforo`. Tachado (`<strike>`) si no se puede elegir; los elegibles tienen `<a href="#">`.
- Cambiar de día: pulsar la celda; el UpdatePanel sustituye la lista (esperar a que la anterior se desconecte del DOM). 7 días en una visita ≈ 10 s.
- El calendario deja pulsar días de más de 49 h: salen con libres = aforo porque **aún no han abierto**. Observado: con hora actual 30/09 15:36, el 02/10 tenía reservas hasta las 16:30 y aforo completo desde las 17:00 → apertura = inicio − 49 h (coincide con madrid.es).
- El horario cambia según el día (sábados otro horario) y el aforo según el turno (8–20).
- **Varias actividades por centro con el mismo título:** en piscina hay un bloque por calle. Cada `ul.media-list` va precedido de su `div.media` con `h4` ("Nado libre") y un primer `<p>` con la calle ("Calle central", "Calle CON BORDILLO", "Calle lateral"…; varía por centro). En sala el primer `<p>` es la edad. La app guarda la actividad como "Nado libre · Calle central" y un turno se identifica por día + hora + actividad.
- Estados en la app (`src/core/slots.py`): libre, completo (→ Observar primero), sin_abrir (→ "abre el … a las …"), pasado.
- Siguiente paso a estudiar: qué pasa al pulsar un turno elegible (pantallas hasta la confirmación, pago/monedero).

### Carrito y pago (visto 2026-09-30, sin confirmar nada)

- **Pulsar la hora de un turno elegible lo mete en el carrito** y lleva a `/DeportesWeb/Modulos/VentaServicios/CarritoConfirmar`. No es solo seleccionar.
- La página muestra el turno (actividad, fecha, sala, inicio–fin), precio con descuentos (p. ej. 5,00 € − 20 % EDAD JOVEN = 4,00 €) y total.
- Formas de pago: radios `name="ContentFixedSection_uCarritoConfirmar_payment_method_filter"` **sin ninguno marcado**, en este orden: Tarjeta bancaria, Bizum, Monedero (con "Saldo disponible X,XX €"). Contenedor `#ContentFixedSection_uCarritoConfirmar_divPaymentMethods`.
- Datos del justificante (nombre, apellidos, correo) vienen rellenos y deshabilitados.
- Botones: "Eliminar el carrito", "Confirmar la compra", "Seguir comprando".
- **Regla acordada:** el bot solo paga con Monedero. Si no aparece o el saldo < total → error y aviso; nunca tarjeta ni Bizum.
- El carrito **caduca solo** (el de la exploración ya no estaba ~40 min después). El bot nunca pulsa "Eliminar el carrito" (la usuaria pidió no tocarlo): si no confirma, el turno se queda ahí hasta que caduque.
- Sin ver todavía: qué pasa tras "Confirmar la compra" (justificante/QR).

### Mis reservas y anulación (verificado 2026-09-30 con una anulación real)

- *Mi cuenta* (`/DeportesWeb/Account`, desde el enlace del correo en la cabecera, postback `MiCuenta`) → tarjeta «Entradas de uso libre» → `/DeportesWeb/Modulos/Eventos/Reservas?token=…`: tabla paginada (10 por página) con estado (vacío, «Pendiente», «Anulado», «Asistido»), fecha, día, horas, actividad, sala, centro e importe.
- Cada fila: botón `button[title='Consultar']` (clase `hidden-lg`: se pulsa por JS) → ficha con resumen, «Monedero Pago X €», «Operación N» y botón `#ContentSection_uReservas_uCarritosFicha_btnRefundCart` «Anular» → ventana «¿Seguro que quieres anular el carrito?» con `…uWarningModal_btnYes` «Sí» (hay dos con el mismo id: usar `:visible`).
- Anular una reserva hecha 2 min antes: permitido, y el importe **vuelve al monedero** al momento (*Mi cuenta → Movimientos monedero*: columnas Fecha, Hora (**en UTC**), Concepto, Importe, Saldo).
- Plazos de anulación en otros casos (24 h / 2 h / 10 min) siguen sin verificar. El bot todavía no anula.

### Avisos por Telegram

- Un bot para toda la casa (`TELEGRAM_BOT_TOKEN`); cada perfil vincula su chat desde Perfil → "Vincular Telegram": código de un solo uso (15 min) en `t.me/<bot>?start=<código>`, la web lee los `/start` con `getUpdates` (sin webhook: no hay acceso desde internet) y guarda `telegram_chat_id` en el perfil.
- `src.notify.notify(conn, settings, profile_id, texto)` nunca lanza y nunca debe llevar datos sensibles.

### Reserva (src/core/booking.py + src/portal/booking.py)

- El programador (`src/worker/scheduler.py`, hilo dentro de la web) lanza cada reserva **60 s antes de la apertura** T = inicio − 49 h (o ya, si está abierta): sesión/login y página del centro y día abiertas en ~4 s.
- Mide el desfase del reloj del portal (cabecera `Date`, ±0,5 s; observado **+0,9 s**) y pulsa cuando en el reloj del portal ya son las T (límite inferior del desfase; nunca antes).
- **Carga en la apertura (observado 30/09 17:00):** una recarga de la lista tardó 28 s y en ese tiempo se ocuparon 9 de 12 plazas. Por eso: un clic a las T sobre la página ya preparada; mientras el portal procesa (`Sys.WebForms.PageRequestManager…get_isInAsyncPostBack()`) no se vuelve a pulsar (hasta 45 s); reintentos con clic directo y recarga solo cada 3 fallos.
- **30/09 18:00 (fallo real):** el clic tardó 24 s en volver, la lista de turnos desapareció mientras el portal la redibujaba y una recarga agotó los 20 s → se abandonó. Ahora: clic con `no_wait_after` y vigilancia propia; esperar a que la lista vuelva (20 s); si la página se rompe o hay error, volver a entrar (Home → servicio → centro → día) con espera creciente 2/4/8/15 s (se reinicia cuando el portal responde al clic); preparación con hasta 3 reintentos; acciones de 45 s; captura de cada intento fallido.
- **Ventana de intentos:** `BOOKING_WINDOW_S` (10 min por defecto; entre semana el portal se cae varios minutos en la apertura). Para antes solo si entra en el carrito, el portal confirma 0 plazas o un mensaje definitivo.
- Carrito: se confirma solo si hay **un** elemento y es este turno (fecha y hora), no pide condiciones, aparece Monedero y saldo ≥ total. Si al confirmar sale del dominio del portal (pasarela de tarjeta) → error, no se toca.
- **Verificado con una compra real (30/09, 4,00 €, anulada después):** tras «Confirmar la compra» se llega a `/DeportesWeb/Modulos/VentaServicios/CarritoResultado` con «Monedero Pago 4,00 €», «Operación <número>» y «Añadir a mi calendario» (no hay texto tipo «compra realizada»). Éxito = `CarritoResultado` + «Operación N». Si no, `revisar`. Se guarda HTML/captura en `data/capturas/reservas/job<id>/`.
- Al pulsar la hora, el portal navega al carrito (UpdatePanel → `pageRedirect`): leer la página en ese momento da «Execution context was destroyed»; se trata como "navegando", no como error. Tras «Confirmar», cualquier fallo de lectura es `revisar`, nunca `fallido`.
- Una reserva a la vez **por perfil** (02/10): dos reservas de la misma persona en paralelo comparten carrito y sesión y se pisan («Error al procesar la petición (CODE:…)» en cada página). Anular también espera a que no haya una reserva de ese perfil en marcha. Lo mismo vale para dos copias del bot (PC y Raspberry) con la misma cuenta: solo una encendida.
- Un turno que se queda en el carrito aparece en *Mi cuenta → Entradas de uso libre* como **«Pendiente»** y bloquea volver a pulsarlo: «La sesión seleccionada no permite más de 1 reserva(s) por persona.» El bot entonces **retoma el carrito** con el enlace de la cabecera `#aCarrito` (abrir la URL a mano no vale) y paga solo si el carrito es solo ese turno. Ese mensaje es definitivo: no se reintenta.
- El contador del carrito está en la cabecera: `<span id="spnCarrito" class="badge">1</span>` (sin elementos no aparece). El 30/09 a las 18:00, con el portal saturado, un clic tardó 24 s y **metió el turno en el carrito sin ir a la página del carrito**; el bot no lo vio, el turno caducó sin pagar y a las 18:30 todo clic devolvía «La operación no se puede realizar porque el carrito ya está expirado.» hasta que el portal lo limpió solo (a las 19:34 ya estaba vacío). Por eso el bot mira el contador antes de la apertura y tras cada clic, y ante cualquier aviso que diga «carrito» abre el carrito: si es este turno lo paga; si no, avisa por Telegram y sigue intentándolo. Nunca vacía el carrito. Verificado el 02/10: al abrir desde la cabecera un carrito caducado, el portal lleva a `CarritoResultado` con «✖ Expirado» (y el turno) y lo descarta; justo después ya se pudo reservar otro turno. El bot lo trata como «caducado», no avisa y vuelve a pulsar.
- Con **abono de uso libre** (verificado el 02/10 con dos reservas reales): el carrito no ofrece ninguna forma de pago (ni tarjeta, ni Bizum, ni monedero) y se confirma directamente. El bot lo trata así: 0 formas de pago → confirmar sin elegir nada, siempre que el carrito sea solo ese turno y no pida condiciones. Si tras confirmar sale hacia una pasarela, se para. La página de resultado con abono dice «Confirmado», «Carrito <número>», «ADM USO LIBRE JOVEN 100% (ENTRADA) −5,00 €» y Total 0,00 €, **sin «Operación»**: el bot acepta «Confirmado» + número de carrito. Sin aclarar: el 02/10 el job 27 (sáb 16:00) recibió durante 2 min «No se permiten más de 1 reservas por persona para cada día» (ya había otra el sábado a las 14:00 y quizá un turno pendiente en el carrito) y al clic 73 entró y se confirmó: el límite parece contar lo pendiente en el carrito, no lo ya confirmado. Desde el 02/10 el bot guarda captura (`carrito_no_valido`) cuando un carrito no le cuadra.
- **Modo prueba:** quitado el 02/10 (la usuaria lo encontraba confuso). La columna `jobs.dry_run` queda solo para el historial; si una prueba antigua sigue programada, el bot la cancela en vez de reservar. `DRY_RUN` en `.env` ya no hace nada.
- Siempre avisa por Telegram. Si no hay plaza, el aviso trae [👀 Observar] [Ignorar]; «Observar» crea una vigilancia del mismo turno (`on_free=reservar`). Vigilancias con `avisar` mandan [Reservar ahora].
- Cerca de una apertura (±2 min alrededor de la preparación) no corren precarga ni vigilancias; las reservas no esperan el candado del navegador.

### Rendimiento (medido 2026-09-30)

- El portal tarda 3–10 s en servir una página entera; a veces más (timeout de navegación a 45 s).
- No se descargan imágenes, vídeos ni fuentes (`context.route`), y hay un solo navegador a la vez en el proceso (`_BROWSER_LOCK`).
- Varios centros en una visita (`fetch_slots_many`): ~10 s por centro con la semana entera.
- **Precarga** (`src/core/prefetch.py`) al elegir perfil, al probar la conexión con éxito y al abrir "Reservar": lista de centros si tiene >1 día y turnos de la semana de los **favoritos** del perfil si tienen >10 min. Solo si el perfil tiene la conexión en estado `ok` (tras un login rechazado no reintenta sola). Un hilo, una precarga pendiente por perfil.
- Los tests nunca tocan el portal real: `tests/conftest.py` apaga la precarga y hace fallar cualquier `open_context`.

### Reglas de negocio (fuentes: madrid.es y sede electrónica)

- Las entradas de uso libre se reservan con hasta 49 horas de antelación al inicio del turno.
- Máximo 2 sesiones por día por usuario; con Abono Deporte Madrid, 3.
- Para entrar hay que validar el código QR o mostrar el comprobante de reserva.
- Anulaciones: hay información contradictoria en el portal (hasta 24 h antes según una página, hasta 2 h antes según otra; y las reservas hechas dentro de las últimas 24 h solo se anulan en los 10 minutos posteriores). Verificar en la cuenta real antes de fijar la lógica de vigilancia de bajas.
- El portal tiene un monedero virtual; hay que comprobar si se usa para las entradas de uso libre.
- Los centros de gestión indirecta (Alcántara, Almudena, Barceló, Daoiz y Velarde 2, Escuelas San Antón, Chamartín, Fabián Roncero, La Cebada, Los Prunos, Moscardó, Peña Grande, Pepu Hernández, Vallehermoso) tienen abonos que solo valen en su centro.
- Reglamento de uso de instalaciones y aviso legal del portal: revisar antes de automatizar a gran escala.

## 6. Decisiones técnicas tomadas

- Lenguaje: Python 3.12+.
- Automatización: Playwright (Chromium) para login y navegación, porque la web usa ViewState y tokens dinámicos. Optimizar a HTTP directo solo el paso crítico de reservar y solo si hace falta.
- Web: FastAPI + Jinja + HTMX (ver sección 2).
- Base de datos: SQLite.
- Programación de tareas: un worker propio (por ejemplo APScheduler) que calcula `hora_apertura = hora_turno − 49 h` y lanza el job unos segundos antes.
- Despliegue: Docker + docker-compose en la Raspberry Pi, `TZ=Europe/Madrid`, `restart: unless-stopped`.
- Sesión del portal: guardar `storage_state` de Playwright por usuario, comprobar si sigue válida antes de cada uso y volver a iniciar sesión solo si caducó, con antelación al momento crítico.
- **Layout de código (hito 1):** `src/` es el paquete raíz (`from src.portal import ...`) y los scripts se lanzan como módulo desde la raíz del repo (`python -m scripts.cli`). No hace falta instalar el proyecto como paquete; `pytest` añade la raíz al path (`pythonpath = ["."]` en `pyproject.toml`).
- **Front:** FastAPI + Jinja, instalable como PWA. Descartados Streamlit (malo en móvil y con login/perfiles) y React/React Native (dos proyectos y dos lenguajes para 2 usuarias; la PWA + Telegram cubre el "como una app"). La lógica queda separada de las pantallas para poder añadir una API JSON y una app nativa si algún día hace falta.
- **Front (hito 2):**
  - Formularios HTML normales (POST y redirección), sin HTMX ni JS salvo un `confirm()` al cancelar. HTMX se añadirá cuando haga falta refresco parcial (estados en vivo, turnos reales).
  - La contraseña de casa se crea en la primera visita (`/configurar`), se guarda con scrypt de la stdlib y la sesión es una cookie firmada de 60 días. El secreto de la cookie se genera solo y se guarda en la tabla `app_settings`.
  - SQLite con `sqlite3` de la stdlib (sin ORM), esquema en `src/db/database.py`.
  - PWA: `manifest.webmanifest` y `sw.js` servidos desde la raíz; el service worker no cachea nada a propósito.
  - "Reservar" en cuatro pasos: servicio → polideportivo (lista guardada en la BD, buscador sin tildes, favoritos arriba) → semana con turnos y plazas (foto guardada en `slots`, compartida entre perfiles, se refresca sola si tiene >10 min y como mucho una vez por minuto) → confirmar reservar u observar. La lista se trae del portal con "Actualizar desde el portal" (tablas `centers`, `center_services`, `favorites`).
- **Estructura de `src/api/`:** los routers solo leen el formulario, llaman a `forms` / `db` y renderizan. La validación va en `forms.py` (se prueba sin levantar la web) y el acceso a datos en `src/db/`. Una pantalla nueva = un router nuevo registrado en `app.py`.
- **Configuración (hito 1):** `pydantic-settings` lee `.env` / variables de entorno (`src/config.py`). Las dependencias de la web, el scheduler y Telegram se añadirán en su hito, no antes.

## 7. Arquitectura

```
bot-madrid-movil/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Dockerfile            # hito 11
├── docker-compose.yml    # hito 11
├── .env.example
├── .gitignore
├── src/
│   ├── config.py   # ajustes desde .env
│   ├── portal/     # TODO lo que habla con deportesweb (login, listar turnos, reservar). Aislado a propósito.
│   ├── core/       # jobs, estrategias (alternativas / observar), reintentos, límites
│   ├── worker/     # scheduler y ejecución
│   ├── api/        # FastAPI, plantillas Jinja, estáticos, PWA
│   │   ├── app.py         # create_app(): middleware, estáticos y routers (nada de lógica)
│   │   ├── deps.py        # dependencias: ajustes, conexión BD, require_house → require_profile
│   │   ├── auth.py        # contraseña de casa (scrypt) y estado de la sesión del navegador
│   │   ├── forms.py       # dataclasses de formularios con validate()
│   │   ├── templating.py  # Jinja: filtros, globals, render() y flash()
│   │   ├── routers/       # una pantalla por archivo: house, profiles, booking, jobs, pwa
│   │   ├── templates/
│   │   └── static/
│   ├── notify/     # Telegram
│   └── db/         # modelos, migraciones, cifrado de credenciales
├── tests/          # con fixtures de HTML/respuestas SANEADAS
└── scripts/        # cli.py para probar sin la web
```

Regla clave: si el portal cambia, solo se toca `src/portal/`. El resto trabaja contra una interfaz propia (`login()`, `list_slots()`, `book()`, `session_is_valid()`).

## 8. Modelo de datos (borrador)

- **Profile**: id, nombre visible, color, usuario del portal, contraseña del portal cifrada, `telegram_chat_id`. La sesión de Playwright va en `data/sessions/<id>.json`.
- **Ajustes de la app**: hash de la contraseña de casa y secreto de las cookies de sesión (tabla clave-valor).
- **Center**: nombre del polideportivo y datos para localizarlo en el portal.
- **Job**: user, center, service (`multitrabajo` | `piscina`), fecha/hora del turno, `mode` (`reservar` | `observar`), estado (`dry_run` solo en jobs antiguos).
- **BookStrategy** (solo modo `reservar`): `alt_times` (lista ordenada de horas alternativas) y `fallback_watch` (si el turno está completo en la apertura, pasar a observar).
- **WatchConfig** (modo `observar`, o al pasar a observar desde `reservar`): `on_free` (`reservar` | `avisar`), `interval_s` (mínimo 30), `until` (hora límite), `extra_slots` (otros turnos vigilados a la vez).
- **Attempt**: job, instante, acción, resultado, mensaje (sin datos sensibles).

Estados de un Job en modo `reservar`: `pendiente` → `esperando_apertura` → `reservando` → `reservado` | `vigilando` (si pasa a observar) | `fallido` | `cancelado`.

Estados de un Job en modo `observar`: `pendiente` → `vigilando` → `plaza_liberada` → `reservado` | `avisado` (si `on_free=avisar`) | `expirado` (llegó la hora límite) | `fallido` | `cancelado`.

## 9. Plan por hitos

Construir en este orden y probar cada hito antes de pasar al siguiente. El front se adelanta porque sus primeras pantallas no dependen del portal:

1. Repo, entorno, `.gitignore`, estructura. ✅
2. ✅ Front básico (FastAPI + Jinja, PWA): cuenta de casa, selector de perfiles, crear perfil con credenciales cifradas, formulario "Reservar" que guarda la programación, "Mis reservas" con estado y cancelar. Todavía no habla con el portal.
3. Login por CLI con Playwright y sesión reutilizable, usando las credenciales del perfil.
4. Listar turnos de un centro y un servicio (solo lectura).
5. Reservar con `--dry-run`.
6. Jobs ejecutables por CLI e historial de intentos.
7. Modo `reservar`: programador (reserva en la apertura) y horas alternativas.
8. Modo `observar`: vigilancia de bajas como modo propio (consulta de disponibilidad, intervalo, hora límite, acción al liberarse, deduplicación de consultas) y paso de `reservar` a `observar` como respaldo.
9. Front completo: turnos reales en el formulario, "Observar" como opción principal cuando un turno está completo, historial de intentos.
10. Notificaciones por Telegram.
11. Docker y despliegue en la Pi (+ acceso por VPN).

## 10. Reglas para Claude Code

- Nunca hacer una reserva real sin que la usuaria lo pida de forma explícita en ese momento. En la app, pulsar «Reservar ahora» / «Planificar reserva» es esa petición (ya no hay modo prueba desde el 02/10). Claude, por su cuenta, nunca lanza reservas reales al probar: usa los tests con el portal simulado.
- Nunca commitear secretos ni datos personales: `.env`, `state*.json`, `*.har`, `*.db`, `data/`, capturas con datos de sesión.
- Los HAR, HTML y respuestas que se usen como fixtures deben estar saneados (contraseñas, cookies, tokens, ViewState, correos y nombres sustituidos).
- No añadir dependencias pesadas sin justificarlo (la Pi tiene recursos limitados).
- No intentar saltar captchas, verificaciones ni límites del portal.
- Antes de escribir código que dependa de un paso del portal que aún no conocemos (selección de turno, confirmación, pago), pedir la captura o los datos en vez de inventar selectores o endpoints.
- Tests para la lógica propia (cálculo de la hora de apertura, estrategias, cambio de hora); el portal se mockea con fixtures.
- Mensajes de commit y comentarios en español; nombres de código en inglés.
- Tras cada hito, actualizar este archivo con lo verificado y lo que cambie.

## 11. Preguntas abiertas (a resolver mirando el portal real)

- ¿Hay captcha o verificación adicional en el login o al reservar?
- ¿A qué hora exacta se abre cada turno (¿siempre 49 h antes, o en tandas)? ¿Las 49 h son de reloj real o de hora de pared (afecta a los días de cambio de hora)?
- ¿Cuál es el plazo real de anulación (2 h o 24 h)?
- ¿Qué pasos y peticiones hay entre elegir un turno y la confirmación (incluido el QR/comprobante)?
- ¿El `token` de `AltaEventos` caduca? ¿Cambia en cada navegación?
- ¿Cómo indica el portal que un turno está completo y cómo se lee la disponibilidad (plazas libres, botón desactivado, mensaje)? ¿Se puede consultar sin abrir el flujo de reserva, con una petición ligera?
- ¿Hasta qué momento se pueden liberar plazas por anulación? Decide la duración útil de la vigilancia.
- ¿Cómo aparece el nado libre en el portal y qué diferencias hay entre polideportivos?
- ¿Se paga con monedero, abono o al entrar?
- Modelo de Raspberry Pi y RAM disponible (decide si se puede mantener un navegador abierto).

## 12. Comandos

```bash
# entorno (Windows: .venv\Scripts\activate; Linux/Pi: source .venv/bin/activate)
python -m venv .venv
pip install -e ".[dev]"     # o: uv sync
python -m playwright install chromium   # a partir del hito 2

# CLI
python -m scripts.cli --help
python -m scripts.cli web              # http://127.0.0.1:8000
python -m scripts.cli web --host 0.0.0.0 --reload   # verla desde el móvil en la misma red
python -m playwright install chromium        # una vez
python -m scripts.cli login --perfil <nombre>  # prueba la conexión (un intento, sin bucles)
python -m scripts.cli capturar --perfil <nombre> [/DeportesWeb/Home ...]  # guarda HTML en data/capturas/

# tests
pytest

# levantar todo en local (hito 11)
docker compose up --build
```
