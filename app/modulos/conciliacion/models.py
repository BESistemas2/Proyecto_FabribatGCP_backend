# app/modulos/conciliacion/models.py
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import Column, String, Integer, Numeric, Date, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import BasePG


def hora_ecuador_naive():
    return datetime.now(ZoneInfo("America/Guayaquil")).replace(tzinfo=None)


class Acta(BasePG):
    __tablename__ = "actas"
    __table_args__ = {"schema": "conciliacion"}

    id_acta = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_cuenta = Column(
        UUID(as_uuid=True), 
        ForeignKey("bancos.cuentas_bancarias.id_cuenta", onupdate="CASCADE", ondelete="RESTRICT"), 
        nullable=True
    )
    cuenta_contable = Column(String(50), nullable=False)
    periodo = Column(String(50), nullable=False)  # <-- Ampliado a String(50)
    saldo_banco = Column(Numeric(18, 2), default=0.00, nullable=False)
    saldo_libros = Column(Numeric(18, 2), default=0.00, nullable=False)
    total_conciliado = Column(Numeric(18, 2), default=0.00, nullable=False)
    creado_por = Column(String(100), nullable=False)
    estado = Column(String(20), default="BORRADOR", nullable=False)
    created_at = Column(DateTime, default=hora_ecuador_naive)
    updated_at = Column(DateTime, default=hora_ecuador_naive, onupdate=hora_ecuador_naive)

    cuenta = relationship("CuentaBancaria")
    partidas = relationship("PartidaTransito", back_populates="acta", cascade="all, delete-orphan")


class PartidaTransito(BasePG):
    __tablename__ = "partidas_transito"
    __table_args__ = {"schema": "conciliacion"}

    id_partida = Column(Integer, primary_key=True, autoincrement=True)
    id_acta = Column(
        UUID(as_uuid=True), 
        ForeignKey("conciliacion.actas.id_acta", ondelete="CASCADE"), 
        nullable=False
    )
    origen_dato = Column(String(20), nullable=False)
    fecha_transaccion = Column(Date, nullable=False)
    documento_referencia = Column(String(100), nullable=True)
    descripcion = Column(String(255), nullable=True)
    monto = Column(Numeric(18, 2), default=0.00, nullable=False)
    created_at = Column(DateTime, default=hora_ecuador_naive)

    acta = relationship("Acta", back_populates="partidas")