# Dockerfile - Backend Fabribat (FastAPI)
# Misma versión que el entorno de desarrollo (requirements.txt se generó con 3.14)
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    WEB_CONCURRENCY=2 \
    FORWARDED_ALLOW_IPS=127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16

WORKDIR /workspace

# Dependencias primero para aprovechar la caché de capas
# (todas las librerías traen wheels: no hace falta build-essential)
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
# El backend compara esta carpeta con public.schema_migrations al arrancar
COPY migrations ./migrations

# Usuario sin privilegios
RUN useradd --system --uid 10001 --no-create-home appuser && chown -R appuser /workspace
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/health', timeout=4)" || exit 1

# IP real del cliente: X-Forwarded-For solo se acepta cuando la conexión viene de un proxy de
# la red interna de Docker (Traefik). Con '*' cualquiera podría falsear su IP.
# Varios workers para que una carga o conciliación larga no bloquee al resto de usuarios
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers ${WEB_CONCURRENCY} --proxy-headers --forwarded-allow-ips=\"${FORWARDED_ALLOW_IPS}\" --timeout-keep-alive 75"]
