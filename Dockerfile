# syntax=docker/dockerfile:1

# --- Build: dependências isoladas em um venv -------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt

# --- Runtime: imagem mínima, usuário não-root ------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    UVICORN_WORKERS=1 \
    SHUTDOWN_TIMEOUT_SECONDS=30

RUN groupadd --gid 10001 nexa \
    && useradd --uid 10001 --gid nexa --no-create-home --shell /usr/sbin/nologin nexa

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=nexa:nexa app ./app

# Volume persistente do SQLite (`/app/data/nexa.db`); o diretório precisa
# pertencer ao usuário sem privilégios para o WAL e os backups funcionarem.
RUN mkdir -p /app/data && chown nexa:nexa /app/data

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

# O prazo de shutdown vem de SHUTDOWN_TIMEOUT_SECONDS (RNF-CON-04); `exec`
# garante que o uvicorn receba o SIGTERM diretamente como PID 1.
CMD ["sh", "-c", "exec python -m app"]
