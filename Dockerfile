# Dockerfile
FROM python:3.11-slim

# Evitar que Python escriba archivos .pyc en el disco
ENV PYTHONDONTWRITEBYTECODE 1
# Evitar que Python guarde en búfer las salidas de consola (logs en tiempo real)
ENV PYTHONUNBUFFERED 1

WORKDIR /workspace

# Instalar dependencias del sistema necesarias para compilar ciertas librerías
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /lib/apt/lists/*

# Copiar e instalar requerimientos primero (aprovecha la caché de capas de Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar todo el código fuente del backend al contenedor
COPY . .

# Comando de arranque optimizado para Cloud Run usando Gunicorn
# Escucha dinámicamente en el puerto que Google defina ($PORT) con hilos concurrentes
CMD exec gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 0 app.main:app