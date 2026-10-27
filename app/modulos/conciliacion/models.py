# app/modulos/conciliacion/models.py
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.core.database import BasePG


def hora_ecuador_naive():
    return datetime.now(ZoneInfo("America/Guayaquil")).replace(tzinfo=None)


class Acta(BasePG):
    __tablename__ = "actas"
    __table_args__ = (
        UniqueConstraint("id_cuenta", "periodo", name="uq_actas_cuenta_periodo"),
        {"schema": "conciliacion"},
    )

    id_acta = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_cuenta = Column(
        UUID(as_uuid=True), 
        ForeignKey("bancos.cuentas_bancarias.id_cuenta", onupdate="CASCADE", ondelete="RESTRICT"), 
        nullable=True
    )
    cuenta_contable = Column(String(50), nullable=False)
    periodo = Column(String(50), nullable=False)  # AAAA-MM
    fecha_inicio = Column(Date, nullable=True)
    fecha_fin = Column(Date, nullable=True)
    saldo_banco = Column(Numeric(18, 2), default=0.00, nullable=False)
    saldo_libros = Column(Numeric(18, 2), default=0.00, nullable=False)
    total_conciliado = Column(Numeric(18, 2), default=0.00, nullable=False)
    creado_por = Column(String(100), nullable=False)
    estado = Column(String(20), default="BORRADOR", nullable=False)  # BORRADOR | CERRADO
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

class CorteInicial(BasePG):
    """Punto de partida de la conciliación de una cuenta (migración 008)."""
    __tablename__ = "cortes_iniciales"
    __table_args__ = {"schema": "conciliacion"}

    id_corte = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_cuenta = Column(UUID(as_uuid=True), ForeignKey("bancos.cuentas_bancarias.id_cuenta", ondelete="CASCADE"),
                       nullable=False, unique=True)
    fecha_corte = Column(Date, nullable=False)
    saldo_banco = Column(Numeric(18, 2), nullable=False)
    saldo_libros = Column(Numeric(18, 2), nullable=False)
    observacion = Column(String(500), nullable=True)
    creado_por = Column(String(100), nullable=False)
    actualizado_por = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=hora_ecuador_naive)
    updated_at = Column(DateTime, default=hora_ecuador_naive, onupdate=hora_ecuador_naive)

    partidas = relationship("PartidaInicial", back_populates="corte", cascade="all, delete-orphan",
                            order_by="PartidaInicial.id_partida")


class PartidaInicial(BasePG):
    __tablename__ = "partidas_iniciales"
    __table_args__ = {"schema": "conciliacion"}

    id_partida = Column(Integer, primary_key=True, autoincrement=True)
    id_corte = Column(UUID(as_uuid=True), ForeignKey("conciliacion.cortes_iniciales.id_corte", ondelete="CASCADE"),
                      nullable=False)
    categoria = Column(String(40), nullable=False)
    fecha = Column(Date, nullable=False)
    detalle = Column(String(255), nullable=False)
    referencia = Column(String(100), nullable=True)
    valor = Column(Numeric(18, 2), nullable=False)
    observacion = Column(String(255), nullable=True)
    estado = Column(String(20), nullable=False, default="PENDIENTE")
    created_at = Column(DateTime, default=hora_ecuador_naive)

    corte = relationship("CorteInicial", back_populates="partidas")
