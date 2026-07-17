from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import (
    MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_PORT, MYSQL_INSTANCE, DB_NAMES
)

# Declaración Base para los Modelos ORM
Base = declarative_base()

def _create_db_engine(db_name_key):
    """
    Crea el motor de SQLAlchemy para un dominio específico,
    manejando dinámicamente si estamos en local o en GCP Cloud Run.
    """
    db_name = DB_NAMES.get(db_name_key)
    if not db_name:
        raise ValueError(f"Dominio de base de datos '{db_name_key}' no definido en config.py")

    # Conexión para Google Cloud Run (usando socket UNIX de Cloud SQL Auth Proxy)
    if MYSQL_INSTANCE:
        db_url = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@/{db_name}?unix_socket=/cloudsql/{MYSQL_INSTANCE}"
    # Conexión para desarrollo local (TCP/IP estándar)
    else:
        db_url = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{db_name}"

    # pool_pre_ping=True evita errores de "conexión caída" (muy común en Cloud Run)
    return create_engine(
        db_url, 
        pool_pre_ping=True, 
        pool_size=5, 
        max_overflow=10
    )

# 1. Instanciar los motores para cada base de datos lógica de Fabribat
engine_core = _create_db_engine('core')
engine_bancos = _create_db_engine('bancos')
engine_cobranzas = _create_db_engine('cobranzas')

# 2. Configurar las fábricas de sesiones (Sessions)
SessionCore = sessionmaker(autocommit=False, autoflush=False, bind=engine_core)
SessionBancos = sessionmaker(autocommit=False, autoflush=False, bind=engine_bancos)
SessionCobranzas = sessionmaker(autocommit=False, autoflush=False, bind=engine_cobranzas)

# 3. Helpers para inyectar sesiones limpias en tus repositorios
def get_core_session():
    """Retorna una nueva sesión para fabri_core."""
    return SessionCore()

def get_bancos_session():
    """Retorna una nueva sesión para fabri_bancos."""
    return SessionBancos()

def get_cobranzas_session():
    """Retorna una nueva sesión para fabri_cobranzas."""
    return SessionCobranzas()