# app/utils/storage.py
import io
import logging
from functools import lru_cache
from typing import Optional

from minio import Minio

from app.core.config import MINIO_CONF

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _cliente_minio() -> Minio:
    """Cliente único y reutilizable (se crea en el primer uso)."""
    return Minio(
        endpoint=MINIO_CONF["endpoint"],
        access_key=MINIO_CONF["access_key"],
        secret_key=MINIO_CONF["secret_key"],
        secure=MINIO_CONF.get("secure", False),
    )


@lru_cache(maxsize=8)
def _asegurar_bucket(bucket: str) -> None:
    client = _cliente_minio()
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def upload_to_minio(file_data: bytes, object_name: str,
                    content_type: str = "application/octet-stream") -> Optional[str]:
    """
    Sube un archivo en memoria al bucket configurado.
    Devuelve la URL del objeto, o None si falla (el detalle queda en el log).
    """
    bucket = MINIO_CONF["bucket"]
    try:
        _asegurar_bucket(bucket)
        _cliente_minio().put_object(
            bucket_name=bucket,
            object_name=object_name,
            data=io.BytesIO(file_data),
            length=len(file_data),
            content_type=content_type,
        )
    except Exception:
        _asegurar_bucket.cache_clear()
        logger.exception("Fallo al subir %s a MinIO", object_name)
        return None

    esquema = "https" if MINIO_CONF.get("secure", False) else "http"
    return f"{esquema}://{MINIO_CONF['endpoint']}/{bucket}/{object_name}"
