# app/utils/storage.py
import io
import logging
from minio import Minio
from app.core.config import MINIO_CONF

def upload_to_minio(file_data: bytes, object_name: str, content_type: str = 'application/octet-stream') -> str:
    """
    Sube un archivo en memoria (bytes) directamente al bucket de MinIO/S3.
    Retorna la URL de acceso directo del archivo o None si falla.
    """
    try:
        # Inicializar el cliente oficial con la configuración de tu core
        client = Minio(
            endpoint=MINIO_CONF['endpoint'],
            access_key=MINIO_CONF['access_key'],
            secret_key=MINIO_CONF['secret_key'],
            secure=MINIO_CONF.get('secure', True)
        )
        
        bucket = MINIO_CONF['bucket']
        
        # Verificar si el bucket existe, si no, lo creamos
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            
        # Subir el flujo de datos en memoria
        data_stream = io.BytesIO(file_data)
        client.put_object(
            bucket_name=bucket,
            object_name=object_name,
            data=data_stream,
            length=len(file_data),
            content_type=content_type
        )
        
        # Construir la URL pública de acceso al recurso
        secure_prefix = "https" if MINIO_CONF.get('secure', True) else "http"
        return f"{secure_prefix}://{MINIO_CONF['endpoint']}/{bucket}/{object_name}"
        
    except Exception as e:
        logging.error(f"Fallo crítico al subir archivo físico a MinIO: {str(e)}")
        return None