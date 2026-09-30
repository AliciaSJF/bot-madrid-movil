# bot-madrid-movil

Bot personal para reservar entradas de uso libre (sala multitrabajo y nado libre) en los polideportivos municipales de Madrid a través de `deportesweb.madrid.es`. Corre en una Raspberry Pi con Docker y se maneja desde una web pensada para el móvil.

Uso doméstico para dos personas. El contexto completo, las decisiones y el plan por hitos están en [CLAUDE.md](CLAUDE.md).

## Arranque rápido

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env             # y rellenar FERNET_KEY
pytest
python -m scripts.cli web      # abre http://127.0.0.1:8000
```
