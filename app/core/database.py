import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core import config  # Importa las configuraciones de config.py

# ==========================================
# 1. MOTOR MYSQL (LEGACY - COBRANZAS)
# ==========================================
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "root_password")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "fabribat_cobranzas")

CLOUDSQL_CONNECTION_NAME = os.getenv("CLOUD_SQL_CONNECTION_NAME")

if CLOUDSQL_CONNECTION_NAME:
    DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASS}@/{DB_NAME}?unix_socket=/cloudsql/{CLOUDSQL_CONNECTION_NAME}"
else:
    DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
    pool_pre_ping=True
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()  # DeclarativeBase para MySQL

def get_db_session():
    """Retorna sesión MySQL (Cobranzas / AppSheet legacy)."""
    return SessionLocal()


# ==========================================
# 2. MOTOR POSTGRESQL (NUEVO - FINANZAS)
# ==========================================
engine_pg = create_engine(
    config.POSTGRES_URL,
    pool_size=10,
    max_overflow=20,
    pool_recycle=3600,
    pool_pre_ping=True
)

SessionLocalPG = sessionmaker(autocommit=False, autoflush=False, bind=engine_pg)
BasePG = declarative_base()  # DeclarativeBase independiente para el esquema finanzas

def get_db_pg():
    """
    Inyector de sesión PostgreSQL para FastAPI (Módulo Finanzas).
    Uso: db: Session = Depends(get_db_pg)
    """
    db = SessionLocalPG()
    try:
        yield db
    finally:
        db.close()