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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts.cli", description="Bot de reservas Deportes Madrid")
    sub = parser.add_subparsers(dest="command", required=True)

    p_config = sub.add_parser("config", help="mostrar la configuración cargada")
    p_config.set_defaults(func=cmd_config)

    # Hito 2: login --user <perfil>
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
