# WAMO Voice-Core als Container (CPU reicht). Abhängigkeiten exakt aus uv.lock.
# Bauen:  docker build -t wamo-voice-core voice-core/
# Der PC-Host läuft nur unter Windows; im Container ist er abgeschaltet (WAMO_PC_HOST=off).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    WAMO_PC_HOST=off

RUN pip install --no-cache-dir uv==0.13.1

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

# Eigener Benutzer ohne Root-Rechte; nur .jobs (Entwicklungsaufträge) muss beschreibbar sein.
RUN useradd --create-home --uid 10001 wamo \
    && mkdir -p /app/.jobs \
    && chown -R wamo:wamo /app/.jobs
USER wamo

EXPOSE 7860
# Lebenszeichen: Port erreichbar (alle HTTP-Routen verlangen eine Anmeldung)
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 7860), 3).close()"]

CMD ["python", "bot.py", "--host", "0.0.0.0", "--port", "7860"]
