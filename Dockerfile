# Dockerfile - Backend Fabribat (FastAPI)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    WEB_CONCURRENCY=2

WORKDIR /workspace

# Dependencias primero para aprovechar la caché de capas
# (todas las librerías traen wheels: no hace falta build-essential)
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Usuario sin privilegios
RUN useradd --system --uid 10001 --no-create-home appuser && chown -R appuser /workspace
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4)" || exit 1

# Varios workers para que una carga o conciliación larga no bloquee al resto de usuarios
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers ${WEB_CONCURRENCY} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 75"]
