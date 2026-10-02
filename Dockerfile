# Imagen para la Raspberry Pi (arm64); también vale en amd64.
# Un solo proceso: la web arranca dentro el programador, la vigilancia y la escucha de Telegram.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    TZ=Europe/Madrid

WORKDIR /app

# Dependencias primero (leídas de pyproject.toml) para no reinstalarlas en cada cambio de código
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# Chromium de Playwright con sus librerías del sistema (la versión casa con el paquete instalado)
RUN python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY src ./src
COPY scripts ./scripts

# Base de datos, sesiones de Playwright y capturas: siempre en el volumen
ENV DATA_DIR=/app/data
VOLUME ["/app/data"]

EXPOSE 8000

CMD ["python", "-m", "scripts.cli", "web", "--host", "0.0.0.0", "--port", "8000"]
