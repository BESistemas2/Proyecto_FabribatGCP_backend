# app/core/config.py
"""
Configuración central. TODO se lee de variables de entorno.

- Docker / Portainer: variables del stack (archivo stack.env, ver .env.example).
- Desarrollo local: archivo .env en la raíz del backend (no se sube a git).

Los secretos NO tienen valor por defecto: si falta alguno, la aplicación no arranca
y el error lista exactamente qué variables faltan.
"""
import os
from pathlib import Path
from urllib.parse import quote_plus

try:  # Solo para desarrollo local; en Docker las variables ya vienen del entorno
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
except ImportError:  # pragma: no cover
    pass

_FALTANTES: list[str] = []


def get_env(key: str, default: str = "") -> str:
    """Variable opcional (con valor por defecto no sensible)."""
    val = os.environ.get(key)
    return str(val).strip() if val is not None and str(val).strip() else default


def require_env(key: str) -> str:
    """Variable obligatoria. Se acumulan las faltantes para informarlas todas juntas."""
    val = get_env(key)
    if not val:
        _FALTANTES.append(key)
    return val


def env_bool(key: str, default: bool = False) -> bool:
    return get_env(key, str(default)).lower() in ("1", "true", "yes", "si", "sí")


def env_list(key: str, default: str = "") -> list[str]:
    return [v.strip() for v in get_env(key, default).split(",") if v.strip()]


# --- APLICACIÓN ---------------------------------------------------------------
APP_ENV = get_env("APP_ENV", "production")            # production | development
LOG_LEVEL = get_env("LOG_LEVEL", "INFO")
ENABLE_DOCS = env_bool("ENABLE_DOCS", APP_ENV == "development")
CORS_ORIGINS = env_list("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")

# --- AUTENTICACIÓN -------------------------------------------------------------
JWT_SECRET_KEY = require_env("JWT_SECRET_KEY")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(get_env("JWT_EXPIRE_HOURS", "8"))
JWT_ISSUER = "fabribat-api"
GOOGLE_CLIENT_ID = require_env("GOOGLE_CLIENT_ID")   # el mismo que NEXT_PUBLIC_GOOGLE_CLIENT_ID del frontend
ALLOWED_DOMAIN = get_env("ALLOWED_DOMAIN", "bateriasecuador.com").lower()

# --- MYSQL (legacy cobranzas; solo se conecta si se usa ese módulo) -------------
MYSQL_HOST = get_env("MYSQL_G_HOST", "localhost")
MYSQL_USER = get_env("MYSQL_G_COBRANZAS_USER", "")
MYSQL_PASSWORD = get_env("MYSQL_S_COBRANZAS_PASSWORD", "")
MYSQL_PORT = int(get_env("MYSQL_G_PORT", "3306"))
MYSQL_INSTANCE = get_env("MYSQL_G_INSTANCE_CONNECTION_NAME", "")

DB_NAMES = {
    "core": get_env("MYSQL_DB_CORE", "fabribat_core"),
    "bancos": get_env("MYSQL_DB_BANCOS", "fabribat_bancos"),
    "cobranzas": get_env("MYSQL_DB_COBRANZAS", "fabribat_cobranzas"),
}

# --- MINIO (S3) -----------------------------------------------------------------
MINIO_CONF = {
    "endpoint": require_env("MINIO_G_ENDPOINT"),
    "access_key": require_env("MINIO_S_ACCESS_KEY"),
    "secret_key": require_env("MINIO_S_SECRET_KEY"),
    "bucket": get_env("MINIO_G_BUCKET_COBRANZAS", "appsheet-cobranzas-files"),
    # true cuando MinIO se publique con TLS (recomendado si viaja por Internet)
    "secure": env_bool("MINIO_G_SECURE", False),
}

# --- DYNAMICS 365 BUSINESS CENTRAL (AZURE AD) -----------------------------------
AZURE_CONFIG = {
    "client_id": require_env("BC_S_AZURE_CLIENT_ID"),
    "client_secret": require_env("BC_S_AZURE_CLIENT_SECRET"),
    "tenant_id": require_env("BC_S_AZURE_TENANT_ID"),
    "scope": get_env("BC_G_AZURE_SCOPE", "https://api.businesscentral.dynamics.com/.default"),
}

MS_LOGIN_BASE = get_env("BC_G_MS_LOGIN_BASE_URL", "https://login.microsoftonline.com")
BC_API_BASE = get_env("BC_G_API_BASE_URL", "https://api.businesscentral.dynamics.com/v2.0")

BC_ENV = {
    "prd": get_env("BC_G_ENV_PRD", "Production"),
    "sbx": get_env("BC_G_ENV_SBX", "Sandbox"),
}

_tenant = AZURE_CONFIG["tenant_id"]
BC_CONFIG = {
    "base_url_prd": f"{BC_API_BASE}/{_tenant}/{BC_ENV['prd']}/api/v2.0",
    "base_url_sbx": f"{BC_API_BASE}/{_tenant}/{BC_ENV['sbx']}/api/v2.0",
    "odata_url_prd": f"{BC_API_BASE}/{_tenant}/{BC_ENV['prd']}/ODataV4",
    "odata_url_sbx": f"{BC_API_BASE}/{_tenant}/{BC_ENV['sbx']}/ODataV4",
    "company_id": get_env("BC_G_COMPANY_ID", "53b767df-76fa-ed11-8f6e-6045bd3ab37b"),
    "company_name": get_env("BC_G_COMPANY_NAME", "FABRIBAT"),
    "journal_template": get_env("BC_G_JOURNAL_TEMPLATE_RECEPEFECT", "CASHRCPT"),
    "journal_batch": get_env("BC_G_JOURNAL_BATCH_COBRANZAS", "CCOBROAPP"),
}

# --- POSTGRESQL (FINANZAS) ------------------------------------------------------
PG_HOST = require_env("PG_G_HOST")
PG_USER = require_env("PG_G_USER")
PG_PASSWORD = quote_plus(require_env("PG_S_PASSWORD"))
PG_PORT = int(get_env("PG_G_PORT", "5432"))
PG_DB = get_env("PG_G_DB", "fabribat_db")
PG_SCHEMA = get_env("PG_G_SCHEMA", "bancos,conciliacion,public")
# disable | prefer | require | verify-full. Si la BD está en otra red (GCP), usar require.
PG_SSLMODE = get_env("PG_G_SSLMODE", "prefer")
PG_POOL_SIZE = int(get_env("PG_POOL_SIZE", "5"))
PG_MAX_OVERFLOW = int(get_env("PG_MAX_OVERFLOW", "5"))

POSTGRES_URL = (
    f"postgresql+psycopg2://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"
    f"?options=-csearch_path%3D{PG_SCHEMA}&sslmode={PG_SSLMODE}"
)

# --- CORREO (SMTP) ----------------------------------------------------------------
SMTP_HOST = require_env("SMTP_HOST")
SMTP_PORT = int(get_env("SMTP_PORT", "465"))      # 465 = SSL directo
SMTP_USER = require_env("SMTP_USER")
SMTP_PASSWORD = require_env("SMTP_PASSWORD")
SMTP_FROM_NAME = get_env("SMTP_FROM_NAME", "Conciliación Fabribat")
SMTP_TIMEOUT = int(get_env("SMTP_TIMEOUT", "30"))
# Dominios a los que se pueden enviar informes (evita usar el servidor como relé abierto)
EMAIL_DOMINIOS_PERMITIDOS = [d.lower() for d in env_list("EMAIL_DOMINIOS_PERMITIDOS", "bateriasecuador.com,fabribat.com")]

# --- VALIDACIÓN FINAL -------------------------------------------------------------
if _FALTANTES:
    raise RuntimeError(
        "Faltan variables de entorno obligatorias: " + ", ".join(_FALTANTES)
        + ". Revisa .env.example."
    )
if len(JWT_SECRET_KEY) < 32:
    raise RuntimeError("JWT_SECRET_KEY debe tener al menos 32 caracteres (usa: openssl rand -hex 32).")
