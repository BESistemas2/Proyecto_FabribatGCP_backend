"""Variables de entorno ficticias para correr los tests sin .env ni conexión a servicios.

Solo se usan si la variable no existe. Ningún valor aquí es real.
"""
import os

_FICTICIAS = {
    "JWT_SECRET_KEY": "ficticio-solo-para-tests-ficticio-solo-para-tests-0123456789abcdef",
    "GOOGLE_CLIENT_ID": "test.apps.googleusercontent.com",
    "MINIO_G_ENDPOINT": "localhost:9000",
    "MINIO_S_ACCESS_KEY": "test",
    "MINIO_S_SECRET_KEY": "test",
    "BC_S_AZURE_CLIENT_ID": "00000000-0000-0000-0000-000000000000",
    "BC_S_AZURE_CLIENT_SECRET": "test",
    "BC_S_AZURE_TENANT_ID": "00000000-0000-0000-0000-000000000000",
    "PG_G_HOST": "localhost",
    "PG_G_USER": "test",
    "PG_S_PASSWORD": "test",
    "SMTP_HOST": "localhost",
    "SMTP_USER": "test",
    "SMTP_PASSWORD": "test",
}

for clave, valor in _FICTICIAS.items():
    os.environ.setdefault(clave, valor)
