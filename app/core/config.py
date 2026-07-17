import os

def get_env(key, default=''):
    val = os.environ.get(key, default)
    if val: 
        return str(val).strip()
    return default

# --- CONFIGURACIÓN DE MYSQL (GCP) ---
MYSQL_HOST = get_env('MYSQL_G_HOST', 'localhost')
MYSQL_USER = get_env('MYSQL_G_COBRANZAS_USER', 'root')
MYSQL_PASSWORD = get_env('MYSQL_S_COBRANZAS_PASSWORD', '')
MYSQL_PORT = int(get_env('MYSQL_G_PORT', '3306') or 3306)
MYSQL_INSTANCE = get_env('MYSQL_G_INSTANCE_CONNECTION_NAME', '')

# --- DOMINIOS DE BASES DE DATOS (FABRIBAT) ---
DB_NAMES = {
    'core': get_env('MYSQL_DB_CORE', 'fabribat_core'),
    'bancos': get_env('MYSQL_DB_BANCOS', 'fabribat_bancos'),
    'cobranzas': get_env('MYSQL_DB_COBRANZAS', 'fabribat_cobranzas')
}

# --- CONFIGURACIÓN DE MINIO (S3) ---
MINIO_CONF = {
    'endpoint': get_env('MINIO_G_ENDPOINT', 'http://34.31.181.156:9000'), 
    'access_key': get_env('MINIO_S_ACCESS_KEY'),
    'secret_key': get_env('MINIO_S_SECRET_KEY'),
    'bucket': get_env('MINIO_G_BUCKET_COBRANZAS')
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
    'company_name': get_env('BC_G_COMPANY_NAME'),
    'journal_template': get_env('BC_G_JOURNAL_TEMPLATE_RECEPEFECT', 'CASHRCPT'),
    'journal_batch': get_env('BC_G_JOURNAL_BATCH_COBRANZAS', 'CCOBROAPP')
}