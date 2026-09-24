import os
from urllib.parse import quote_plus

def get_env(key, default=''):
    val = os.environ.get(key, default)
    if val: 
        return str(val).strip()
    return default

# --- CONFIGURACIÓN DE MYSQL (GCP) ---
MYSQL_HOST = get_env('MYSQL_G_HOST')
MYSQL_USER = get_env('MYSQL_G_COBRANZAS_USER')
MYSQL_PASSWORD = get_env('MYSQL_S_COBRANZAS_PASSWORD')
MYSQL_PORT = int(get_env('MYSQL_G_PORT', '3306'))
MYSQL_INSTANCE = get_env('MYSQL_G_INSTANCE_CONNECTION_NAME')

# --- DOMINIOS DE BASES DE DATOS (FABRIBAT) ---
DB_NAMES = {
    'core': get_env('MYSQL_DB_CORE', 'fabribat_core'),
    'bancos': get_env('MYSQL_DB_BANCOS', 'fabribat_bancos'),
    'cobranzas': get_env('MYSQL_DB_COBRANZAS', 'fabribat_cobranzas')
}

# --- CONFIGURACIÓN DE MINIO (S3) ---
MINIO_CONF = {
    'endpoint': get_env('MINIO_G_ENDPOINT', '34.31.181.156:9000'), 
    'access_key': get_env('MINIO_S_ACCESS_KEY','admin'),
    'secret_key': get_env('MINIO_S_SECRET_KEY'),
    'bucket': get_env('MINIO_G_BUCKET_COBRANZAS','appsheet-cobranzas-files')
}

# --- CONFIGURACIÓN DE DYNAMICS 365 BC (AZURE) ---
AZURE_CONFIG = {
    'client_id': get_env('BC_S_AZURE_CLIENT_ID'),
    'client_secret': get_env('BC_S_AZURE_CLIENT_SECRET'),
    'tenant_id': get_env('BC_S_AZURE_TENANT_ID'),
    'scope': get_env('BC_G_AZURE_SCOPE', 'https://api.businesscentral.dynamics.com/.default')
}

MS_LOGIN_BASE = get_env('BC_G_MS_LOGIN_BASE_URL', 'https://login.microsoftonline.com')
BC_API_BASE = get_env('BC_G_API_BASE_URL', 'https://api.businesscentral.dynamics.com/v2.0')

BC_ENV = {
    'prd': get_env('BC_G_ENV_PRD', 'Production'),
    'sbx': get_env('BC_G_ENV_SBX', 'Sandbox')
}

BC_CONFIG = {
    'base_url_prd': f"{BC_API_BASE}/{AZURE_CONFIG['tenant_id']}/{BC_ENV['prd']}/api/v2.0",
    'base_url_sbx': f"{BC_API_BASE}/{AZURE_CONFIG['tenant_id']}/{BC_ENV['sbx']}/api/v2.0",
    'odata_url_prd': f"{BC_API_BASE}/{AZURE_CONFIG['tenant_id']}/{BC_ENV['prd']}/ODataV4",
    'odata_url_sbx': f"{BC_API_BASE}/{AZURE_CONFIG['tenant_id']}/{BC_ENV['sbx']}/ODataV4",
    'company_id': get_env('BC_G_COMPANY_ID'),
    'company_name': get_env('BC_G_COMPANY_NAME','FABRIBAT'),
    'journal_template': get_env('BC_G_JOURNAL_TEMPLATE_RECEPEFECT', 'CASHRCPT'),
    'journal_batch': get_env('BC_G_JOURNAL_BATCH_COBRANZAS', 'CCOBROAPP')
}

# --- CONFIGURACIÓN DE POSTGRESQL (FINANZAS / LOCAL & GCP) ---
PG_HOST = get_env('PG_G_HOST', '34.31.181.156')
PG_USER = get_env('PG_G_USER', 'postgres')
PG_PASSWORD = quote_plus(get_env('PG_S_PASSWORD'))  # Reemplaza por tu clave de Postgres local
PG_PORT = int(get_env('PG_G_PORT', '5432') or 5432)
PG_DB = get_env('PG_G_DB', 'fabribat_db')
PG_SCHEMA = get_env('PG_G_SCHEMA', 'bancos,conciliacion,public')

# URL de conexión SQLAlchemy con search_path al esquema finanzas
POSTGRES_URL = (
    f"postgresql+psycopg2://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"
    f"?options=-csearch_path%3D{PG_SCHEMA}"
)