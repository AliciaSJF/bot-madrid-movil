# Bot de reservas – Deportes Madrid (uso libre)

Contexto para Claude Code. Léelo entero antes de tocar código. Si algo de aquí choca con lo que veas en el repo o en el portal, para y pregunta en vez de asumir.

## 0. Estado actual

| Hito | Estado |
|---|---|
| 1. Repo, entorno, `.gitignore`, estructura | ✅ hecho |
| 2. Front básico: cuenta de casa, perfiles, reservar, mis reservas (sin portal) | ✅ hecho (rama `feat/front-perfiles`) |
| 3. Login por CLI con Playwright | ⏳ siguiente |
| 4–11 | pendiente |

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
7. Modo prueba (`dry_run`) que recorre todo el flujo sin confirmar la reserva final.

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
- Existe un panel de verificación de login (`uLoginVerification`); no sabemos cuándo se activa. Vigilarlo, sobre todo al entrar desde un dispositivo nuevo (la Pi).
- Tras un login fallido no reintentar en bucle: parar y avisar, para no bloquear la cuenta.

### Sala multitrabajo

- Página de alta: `/DeportesWeb/Modulos/VentaServicios/Eventos/AltaEventos?token=...`.
- El `token` va en la query y probablemente cambia por sesión o navegación. No guardarlo fijo: llegar a la página navegando y leer el enlace.

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
  - Si `DRY_RUN=true` en el servidor, todas las programaciones se guardan en modo prueba aunque el formulario diga otra cosa.
  - El polideportivo es texto libre con sugerencias de los ya usados, hasta que el hito 4 lea la lista real del portal.
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
- **Job**: user, center, service (`multitrabajo` | `piscina`), fecha/hora del turno, `mode` (`reservar` | `observar`), `dry_run`, estado.
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

- Nunca hacer una reserva real sin que la usuaria lo pida de forma explícita en ese momento. Por defecto, `dry_run=True`.
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
python -m scripts.cli login --user <perfil>   # hito 3

# tests
pytest

# levantar todo en local (hito 11)
docker compose up --build
```
