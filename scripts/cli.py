"""CLI para probar el bot sin la web: python -m scripts.cli <comando>."""

import argparse
import sys

from src.config import get_settings


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

    # Hito 3: login --user <perfil>
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
