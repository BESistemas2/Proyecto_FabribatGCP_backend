# app/core/database.py
from functools import lru_cache

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core import config, contexto

# ==========================================
# 1. MYSQL (LEGACY - COBRANZAS)
# Solo se conecta si algún módulo lo usa (hoy cobranzas no está montado).
# ==========================================
Base = declarative_base()  # Base declarativa de los modelos MySQL (app/core/models.py)


@lru_cache(maxsize=1)
def _engine_mysql() -> Engine:
    user = config.get_env("DB_USER") or config.MYSQL_USER
    password = config.get_env("DB_PASS") or config.MYSQL_PASSWORD
    host = config.get_env("DB_HOST") or config.MYSQL_HOST
    port = config.get_env("DB_PORT", str(config.MYSQL_PORT))
    name = config.get_env("DB_NAME", config.DB_NAMES["cobranzas"])
    if not user or not password:
        raise RuntimeError("Faltan DB_USER/DB_PASS para conectar a MySQL (módulo cobranzas).")

    instancia = config.get_env("CLOUD_SQL_CONNECTION_NAME")
    if instancia:
        url = f"mysql+pymysql://{user}:{password}@/{name}?unix_socket=/cloudsql/{instancia}"
    else:
        url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{name}"
    return create_engine(url, pool_size=5, max_overflow=5, pool_recycle=3600, pool_pre_ping=True)


def get_db_session():
    """Sesión MySQL (Cobranzas / AppSheet legacy)."""
    return sessionmaker(autocommit=False, autoflush=False, bind=_engine_mysql())()


# ==========================================
# 2. POSTGRESQL (FINANZAS)
# ==========================================
engine_pg = create_engine(
    config.POSTGRES_URL,
    pool_size=config.PG_POOL_SIZE,
    max_overflow=config.PG_MAX_OVERFLOW,
    pool_recycle=1800,
    pool_pre_ping=True,
    connect_args={"connect_timeout": 10, "application_name": "fabribat-api"},
)

SessionLocalPG = sessionmaker(autocommit=False, autoflush=False, bind=engine_pg)
BasePG = declarative_base()  # Base declarativa de los esquemas bancos / conciliacion


# Marca de la aplicación en PostgreSQL (lo usa el trigger de auditoría de cambios directos):
# - cada conexión del pool nace con app.origen = 'API';
# - cada transacción recibe el origen, el usuario y el id de solicitud del contexto actual.
# Un cambio hecho sin esa marca (Portainer, psql) se registra como SQL_DIRECTO.
@event.listens_for(engine_pg, "connect")
def _marcar_conexion(dbapi_conn, _registro):
    with dbapi_conn.cursor() as cur:
        cur.execute("SET app.origen = 'API'")
    dbapi_conn.commit()


@event.listens_for(SessionLocalPG, "after_begin")
def _contexto_en_transaccion(_session, _transaction, connection):
    connection.execute(
        text("SELECT set_config('app.origen', :origen, true), set_config('app.usuario', :usuario, true), "
             "set_config('app.id_solicitud', :id_solicitud, true)"),
        contexto.ajustes_pg(),
    )


def get_db_pg():
    """
    Inyector de sesión PostgreSQL para FastAPI.
    Uso: db: Session = Depends(get_db_pg)
    """
    db = SessionLocalPG()
    try:
        yield db
    finally:
        db.close()
