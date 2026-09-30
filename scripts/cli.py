"""CLI para probar el bot sin la web: python -m scripts.cli <comando>."""

import argparse
import sqlite3
import sys

from src.config import get_settings
from src.db import profiles as profiles_repo
from src.db.database import connect, init_db


def cmd_config(_args: argparse.Namespace) -> int:
    """Muestra la configuración efectiva sin revelar secretos."""
    settings = get_settings()
    for name, value in settings.model_dump().items():
        print(f"{name} = {value}")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    """Arranca la web. Por defecto solo en local; --host 0.0.0.0 para verla desde el móvil."""
    import uvicorn

    uvicorn.run("src.api.app:create_app", factory=True, host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    """Prueba la conexión del perfil con el portal (un solo intento) y guarda la sesión."""
    from src.core.connection import check_connection

    settings = get_settings()
    conn = _open_db(settings)
    try:
        profile = _find_profile(conn, args.perfil)
        if profile is None:
            return 1
        print(f"Probando la conexión de «{profile.display_name}»…")
        check = check_connection(conn, settings, profile.id)
        print(f"[{profiles_repo.PORTAL_STATUS_LABELS[check.status]}] {check.message}")
        return 0 if check.ok else 1
    finally:
        conn.close()


def cmd_capture(args: argparse.Namespace) -> int:
    """Guarda HTML y captura de páginas del portal con la sesión del perfil (para estudiarlas)."""
    from src.portal.capture import capture_pages
    from src.portal.errors import PortalError

    settings = get_settings()
    conn = _open_db(settings)
    try:
        profile = _find_profile(conn, args.perfil)
    finally:
        conn.close()
    if profile is None:
        return 1
    try:
        out_dir = capture_pages(settings, profile.id, args.rutas, args.pulsar)
    except PortalError as exc:
        print(f"Error: {exc}")
        return 1
    print(f"Guardado en {out_dir}")
    return 0


def _open_db(settings) -> sqlite3.Connection:
    init_db(settings.data_dir)
    return connect(settings.data_dir)


def _find_profile(conn: sqlite3.Connection, name: str) -> profiles_repo.Profile | None:
    profile = profiles_repo.get_profile_by_name(conn, name)
    if profile is None:
        names = ", ".join(p.display_name for p in profiles_repo.list_profiles(conn)) or "ninguno"
        print(f"No hay ningún perfil «{name}». Perfiles: {names}")
    return profile


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts.cli", description="Bot de reservas Deportes Madrid")
    sub = parser.add_subparsers(dest="command", required=True)

    p_config = sub.add_parser("config", help="mostrar la configuración cargada")
    p_config.set_defaults(func=cmd_config)

    p_web = sub.add_parser("web", help="arrancar la web")
    p_web.add_argument("--host", default="127.0.0.1")
    p_web.add_argument("--port", type=int, default=8000)
    p_web.add_argument("--reload", action="store_true", help="recargar al cambiar el código")
    p_web.set_defaults(func=cmd_web)

    p_login = sub.add_parser("login", help="probar la conexión de un perfil con el portal")
    p_login.add_argument("--perfil", required=True, help="nombre del perfil, como aparece en la web")
    p_login.set_defaults(func=cmd_login)

    p_capture = sub.add_parser("capturar", help="guardar páginas del portal en data/capturas/")
    p_capture.add_argument("--perfil", required=True)
    p_capture.add_argument("rutas", nargs="*", default=["/DeportesWeb/Home"], help="rutas del portal")
    p_capture.add_argument(
        "--pulsar", action="append", default=[], metavar="TÍTULO",
        help="tarjeta de menú a pulsar tras abrir las rutas (se puede repetir, en orden)",
    )
    p_capture.set_defaults(func=cmd_capture)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
