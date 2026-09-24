import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import Column, String, Integer, Boolean, Numeric, Date, DateTime, Text, Time, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.core.database import BasePG

def hora_ecuador_naive():
    return datetime.now(ZoneInfo("America/Guayaquil")).replace(tzinfo=None)


class Institucion(BasePG):
    __tablename__ = "instituciones"
    __table_args__ = {"schema": "bancos"}

    id_institucion = Column(String(50), primary_key=True)
    codigo = Column(Integer, nullable=True)
    nombre = Column(String(255), nullable=False)
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=hora_ecuador_naive)
    updated_at = Column(DateTime(timezone=True), default=hora_ecuador_naive, onupdate=hora_ecuador_naive)
    permite_depositos = Column(Boolean, default=False, nullable=False)
    emite_cheques = Column(Boolean, default=False, nullable=False)
    id_forma_pago = Column(UUID(as_uuid=True), nullable=True)

    cargas_extractos = relationship("CargaExtracto", back_populates="institucion")
    movimientos = relationship("Movimiento", back_populates="institucion")
    cuentas_bancarias = relationship("CuentaBancaria", back_populates="institucion")


class CuentaBancaria(BasePG):
    __tablename__ = "cuentas_bancarias"
    __table_args__ = {"schema": "bancos"}

    id_cuenta = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_institucion = Column(
        String(50), 
        ForeignKey("bancos.instituciones.id_institucion", onupdate="CASCADE", ondelete="SET NULL"), 
        nullable=True
    )
    codigo_banco_bc = Column(String(30), nullable=False, unique=True, index=True)
    nombre_cuenta = Column(String(150), nullable=False)
    numero_cuenta = Column(String(50), nullable=True)
    grupo_registro_bc = Column(String(50), nullable=True, index=True)
    cuenta_contable_bc = Column(String(30), nullable=True, index=True)
    moneda = Column(String(10), default="USD", nullable=False)

    rpa_habilitado = Column(Boolean, default=True, nullable=False)
    rpa_frecuencia = Column(String(20), default="DIARIA", nullable=False)
    rpa_intervalo_minutos = Column(Integer, default=1440, nullable=True)
    rpa_hora_ejecucion = Column(Time, nullable=True)

    auto_match_habilitado = Column(Boolean, default=True, nullable=False)
    auto_match_frecuencia = Column(String(20), default="DIARIA", nullable=False)
    auto_match_intervalo_minutos = Column(Integer, default=1440, nullable=True)
    auto_match_hora = Column(Time, nullable=True)
    
    activo = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime, default=hora_ecuador_naive)
    updated_at = Column(DateTime, default=hora_ecuador_naive, onupdate=hora_ecuador_naive)

    institucion = relationship("Institucion", back_populates="cuentas_bancarias")
    cargas_extractos = relationship("CargaExtracto", back_populates="cuenta")
    movimientos = relationship("Movimiento", back_populates="cuenta")


class CargaExtracto(BasePG):
    __tablename__ = "cargas_extractos"
    __table_args__ = {"schema": "bancos"}

    id_carga = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_cuenta = Column(
        UUID(as_uuid=True), 
        ForeignKey("bancos.cuentas_bancarias.id_cuenta", onupdate="CASCADE", ondelete="RESTRICT"), 
        nullable=True
    )
    id_institucion = Column(
        String(50), 
        ForeignKey("bancos.instituciones.id_institucion", onupdate="CASCADE", ondelete="RESTRICT"), 
        nullable=True
    )
    nombre_archivo = Column(String(255), nullable=False)
    url_archivo = Column(String(500), nullable=True)
    total_registros_leidos = Column(Integer, default=0)
    registros_importados = Column(Integer, default=0)
    estado = Column(String(50), default="Procesando")
    mensaje_log = Column(Text, nullable=True)
    created_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=hora_ecuador_naive)
    updated_at = Column(DateTime, default=hora_ecuador_naive, onupdate=hora_ecuador_naive)
    hash_archivo = Column(String(64), nullable=True, index=True)

    cuenta = relationship("CuentaBancaria", back_populates="cargas_extractos")
    institucion = relationship("Institucion", back_populates="cargas_extractos")
    movimientos = relationship("Movimiento", back_populates="carga_extracto", cascade="all, delete-orphan")


class Movimiento(BasePG):
    __tablename__ = "movimientos"
    __table_args__ = {"schema": "bancos"}

    id_movimiento = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id_carga = Column(
        UUID(as_uuid=True), 
        ForeignKey("bancos.cargas_extractos.id_carga", onupdate="CASCADE", ondelete="RESTRICT"), 
        nullable=False
    )
    id_cuenta = Column(
        UUID(as_uuid=True), 
        ForeignKey("bancos.cuentas_bancarias.id_cuenta", onupdate="CASCADE", ondelete="RESTRICT"), 
        nullable=False
    )
    id_institucion = Column(
        String(50), 
        ForeignKey("bancos.instituciones.id_institucion", onupdate="CASCADE", ondelete="SET NULL"), 
        nullable=True
    )
    fecha_transaccion = Column(Date, nullable=False)
    numero_referencia = Column(String(100), nullable=False)
    concepto = Column(String(255), nullable=True)
    monto = Column(Numeric(12, 2), nullable=False)
    tipo_movimiento = Column(String(50), nullable=True)
    estado = Column(String(50), default="Pendiente", nullable=False)
    id_valores_recibidos = Column(String(50), nullable=True)
    info_adicional = Column(String(512), nullable=True)
    id_acta = Column(UUID(as_uuid=True), nullable=True)
    regla_cruce = Column(String(50), nullable=True)

    carga_extracto = relationship("CargaExtracto", back_populates="movimientos")
    cuenta = relationship("CuentaBancaria", back_populates="movimientos")
    institucion = relationship("Institucion", back_populates="movimientos")