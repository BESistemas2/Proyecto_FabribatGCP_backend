import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# 1. Variables de entorno apuntando a tu base de datos unificada
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "root_password")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "3306")
# Por defecto apuntamos a la BD monolítica donde viven todas tus tablas
DB_NAME = os.getenv("DB_NAME", "fabribat_cobranzas") 

# 2. Detector de entorno (Local vs Google Cloud Run)
CLOUDSQL_CONNECTION_NAME = os.getenv("CLOUD_SQL_CONNECTION_NAME")

if CLOUDSQL_CONNECTION_NAME:
    # Conexión optimizada por Unix Socket (Exclusivo para GCP)
    DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASS}@/{DB_NAME}?unix_socket=/cloudsql/{CLOUDSQL_CONNECTION_NAME}"
else:
    # Conexión estándar TCP (Para tu máquina local o DBeaver)
    DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# 3. Creación del motor de base de datos único
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
    pool_pre_ping=True  # Evita que las conexiones caídas tumben la app
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 4. Función de sesión unificada
def get_db_session():
    """
    Retorna una sesión limpia de la base de datos única.
    Todos los módulos (bancos, cobranzas, identidad) deben llamar a esta función.
    """
    return SessionLocal()