# app/core/logs.py
"""
Logs técnicos. Cada línea lleva el id_solicitud, que también se guarda en la auditoría:
así un evento de auditoría se puede cruzar con sus logs.

LOG_FORMAT=json (producción): una línea JSON por registro, para Docker / Portainer.
LOG_FORMAT=text (desarrollo): formato legible.
"""
import json
import logging
from datetime import datetime, timezone

from app.core import config, contexto


class _FiltroContexto(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        ctx = contexto.actual()
        record.id_solicitud = ctx.id_solicitud or "-"
        record.usuario = ctx.email or "-"
        return True


class _FormatoJson(logging.Formatter):
    CAMPOS_EXTRA = ("metodo", "ruta", "estado", "duracion_ms", "ip")

    def format(self, record: logging.LogRecord) -> str:
        datos = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "nivel": record.levelname,
            "logger": record.name,
            "mensaje": record.getMessage(),
            "id_solicitud": getattr(record, "id_solicitud", "-"),
            "usuario": getattr(record, "usuario", "-"),
        }
        for campo in self.CAMPOS_EXTRA:
            if hasattr(record, campo):
                datos[campo] = getattr(record, campo)
        if record.exc_info:
            datos["error"] = self.formatException(record.exc_info)
        return json.dumps(datos, ensure_ascii=False, default=str)


def configurar(nombre_proceso: str = "fabribat") -> None:
    """Configura el logger raíz. Llamar una vez al arrancar el proceso."""
    manejador = logging.StreamHandler()
    manejador.addFilter(_FiltroContexto())
    if config.LOG_FORMAT == "json":
        manejador.setFormatter(_FormatoJson())
    else:
        manejador.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] [%(id_solicitud)s] %(message)s"))
    raiz = logging.getLogger()
    raiz.handlers[:] = [manejador]
    raiz.setLevel(config.LOG_LEVEL.upper())
    # Los accesos ya los registra el middleware con id_solicitud y duración
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger(nombre_proceso).debug("Logs configurados (%s)", config.LOG_FORMAT)
