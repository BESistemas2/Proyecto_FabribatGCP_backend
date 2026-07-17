# app/core/models.py
from sqlalchemy import Column, String, Integer, Boolean, DateTime, Date, Numeric, ForeignKey, Text, TIMESTAMP, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base

# =========================================================================
# DOMINIO 1: SECURITY & IDENTITY (fabribat_core)
# =========================================================================

class Perfil(Base):
    __tablename__ = 'Perfiles'
    __table_args__ = {'schema': 'fabribat_core'}

    idPerfil = Column(String(50), primary_key=True)
    nombrePerfil = Column(String(100), nullable=False)
    createdOn = Column(DateTime, nullable=False, server_default=func.now())
    
    # Relación uno a muchos con Usuarios
    usuarios = relationship("Usuario", back_populates="perfil")


class Usuario(Base):
    __tablename__ = 'Usuarios'
    __table_args__ = {'schema': 'fabribat_core'}

    idUsuario = Column(String(50), primary_key=True)
    email = Column(String(255), nullable=False, unique=True)
    idPerfil = Column(String(50), ForeignKey('fabribat_core.Perfiles.idPerfil'), nullable=False)
    activo = Column(Boolean, nullable=False, default=True)
    createdOn = Column(DateTime, nullable=False, server_default=func.now())
    cobradorDynamics = Column(String(50))
    
    # Campos para el MVP del Bot de Telegram y MFA
    telegram_chat_id = Column(String(50), unique=True, nullable=True)
    mfa_secret = Column(String(100), nullable=True) 

    perfil = relationship("Perfil", back_populates="usuarios")


# =========================================================================
# DOMINIO 2: FINANZAS Y BANCOS (fabribat_bancos)
# =========================================================================

class CargaBancaria(Base):
    __tablename__ = 'CargasBancarias'
    __table_args__ = {'schema': 'fabribat_bancos'}

    idCarga = Column(String(36), primary_key=True)
    idInstitucion = Column(String(50), nullable=False)
    nombreArchivo = Column(String(255), nullable=False)
    urlArchivo = Column(String(500))
    totalRegistrosLeidos = Column(Integer, default=0)
    registrosImportados = Column(Integer, default=0)
    estado = Column(String(50), default='Procesando')
    mensajeLog = Column(Text)
    createdBy = Column(String(100), nullable=False)
    createdOn = Column(DateTime, server_default=func.now())
    hashArchivo = Column(String(64), index=True)


class MovimientoBancario(Base):
    __tablename__ = 'MovimientosBancarios'
    __table_args__ = (
        UniqueConstraint('idInstitucion', 'numeroReferencia', 'fechaTransaccion', 'monto', name='unq_movimiento_banco'),
        {'schema': 'fabribat_bancos'}
    )

    idMovimiento = Column(String(36), primary_key=True)
    idCarga = Column(String(36), ForeignKey('fabribat_bancos.CargasBancarias.idCarga'), nullable=False)
    idInstitucion = Column(String(50), nullable=False)
    fechaTransaccion = Column(Date, nullable=False)
    numeroReferencia = Column(String(100), nullable=False)
    concepto = Column(String(255))
    monto = Column(Numeric(12, 2), nullable=False)
    tipoMovimiento = Column(String(50))
    estado = Column(String(20), nullable=False, default='Pendiente')
    infoAdicional = Column(String(512))


# =========================================================================
# DOMINIO 3: COBRANZAS Y TRANSACCIONES (fabribat_cobranzas)
# =========================================================================

class InstitucionFinanciera(Base):
    __tablename__ = 'InstitucionesFinancieras'
    __table_args__ = {'schema': 'fabribat_cobranzas'}

    idInstitucion = Column(String(50), primary_key=True)
    nombre = Column(String(100), nullable=False)


class FormaPago(Base):
    __tablename__ = 'FormasPago'
    __table_args__ = {'schema': 'fabribat_cobranzas'}

    idFormaPago = Column(String(36), primary_key=True)
    bcCode = Column(String(12), nullable=False, unique=True)
    descripcion = Column(String(50), nullable=False)


class Cliente(Base):
    __tablename__ = 'Clientes'
    __table_args__ = {'schema': 'fabribat_cobranzas'}

    idCliente = Column(String(36), primary_key=True)
    bcCustomerNo = Column(String(45))
    tmp_bcSystemId = Column(String(36))
    ruc = Column(String(20), nullable=False, unique=True)
    codigoCliente = Column(Integer)
    nombre = Column(String(550), nullable=False)
    direccion = Column(Text)
    telefono = Column(String(100))
    email = Column(String(250))
    dynamicsGuid = Column(String(36))
    lastSyncOn = Column(DateTime, nullable=False)
    createdBy = Column(Text)
    createdOn = Column(DateTime, nullable=False, server_default=func.now())
    lastModifiedBy = Column(Text)
    lastModifiedOn = Column(DateTime)
    estado = Column(Boolean, default=False)
    idVendedor = Column(Integer)
    cobrador = Column(String(100))
    grupoRegistro = Column(String(25))

    facturas = relationship("Factura", back_populates="cliente")


class Factura(Base):
    __tablename__ = 'Facturas'
    __table_args__ = {'schema': 'fabribat_cobranzas'}

    idFactura = Column(String(36), primary_key=True)
    idCliente = Column(String(36), ForeignKey('fabribat_cobranzas.Clientes.idCliente'))
    tipoDocumento = Column(String(20), default='Invoice')
    bcDocumentNo = Column(String(50))
    facturaOriginal = Column(String(50))
    fechaEmision = Column(Date)
    fechaVencimiento = Column(Date)
    montoTotal = Column(Numeric(10, 2), default=0.00)
    saldoPendiente = Column(Numeric(10, 2), default=0.00)
    origen = Column(String(20), default='BC')
    lastSyncOn = Column(DateTime)

    cliente = relationship("Cliente", back_populates="facturas")


class CierreCaja(Base):
    __tablename__ = 'CierreCaja'
    __table_args__ = {'schema': 'fabribat_cobranzas'}

    idCierreCaja = Column(String(50), primary_key=True)
    fechaCierre = Column(Date, nullable=False)
    idUsuarioCierre = Column(String(50), nullable=False)
    createdOn = Column(TIMESTAMP, nullable=False, server_default=func.now())
    totalEfectivo = Column(Numeric(10, 2), default=0.00)
    totalCheques = Column(Numeric(10, 2), default=0.00)
    totalGeneral = Column(Numeric(10, 2), default=0.00)
    estadoCierre = Column(String(20), default='CERRADO')
    observaciones = Column(Text)
    estadoIntegracion = Column(String(20), default='PENDIENTE')
    logIntegracion = Column(Text)
    caja = Column(String(12))
    urlExcelReporte = Column(String(200))
    codigoCierre = Column(String(25), nullable=False)


class Comprobante(Base):
    __tablename__ = 'Comprobantes'
    __table_args__ = {'schema': 'fabribat_cobranzas'}

    idComprobante = Column(String(50), primary_key=True)
    puntoVenta = Column(String(3), nullable=False)
    puntoEmision = Column(String(3), nullable=False)
    numComprobante = Column(String(100))
    fecha = Column(DateTime, nullable=False)
    codigoCliente = Column(Integer)
    rucCliente = Column(String(20), nullable=False)
    idCliente = Column(String(36))
    cliente = Column(String(550), nullable=False)
    telefono = Column(String(50))
    recibi = Column(Numeric(18, 2), nullable=False)
    recibiLetras = Column(Text, nullable=False)
    credVencidoSinRespaldos = Column(Numeric(18, 2))
    credPorVencerSinRespaldos = Column(Numeric(18, 2))
    saldoSinRespaldo = Column(Numeric(18, 2))
    abonoRespaldo = Column(Numeric(18, 2))
    saldoPendienteCobro = Column(Numeric(18, 2))
    totalChequeTransfer = Column(Numeric(18, 2), nullable=False)
    efectivo = Column(Numeric(18, 2))
    totalT1 = Column(Numeric(18, 2))
    totalT2 = Column(Numeric(18, 2))
    idEstado = Column(Integer, nullable=False)
    createdBy = Column(Text)
    createdOn = Column(DateTime, nullable=False)
    lastModifiedBy = Column(Text)
    lastModifiedOn = Column(DateTime)
    _generarPdfTrigger = Column(DateTime)
    comentario = Column(Text)
    mailCliente = Column(String(250))
    comentarioAdic = Column(String(250))
    ventaContado = Column(Boolean)
    urlPdfComprobante = Column(Text)
    urlPdfComprobante2 = Column(Text, nullable=False)
    idCierreCaja = Column(String(50), ForeignKey('fabribat_cobranzas.CierreCaja.idCierreCaja'))
    syncBC_Status = Column(String(20), default='PENDIENTE')
    syncBC_Date = Column(DateTime)

    valores_recibidos = relationship("ValorRecibido", back_populates="comprobante", cascade="all, delete-orphan")
    pagos_facturas = relationship("PagoFactura", back_populates="comprobante", cascade="all, delete-orphan")


class ValorRecibido(Base):
    __tablename__ = 'ValoresRecibidos'
    __table_args__ = {'schema': 'fabribat_cobranzas'}

    idValoresRecibidos = Column(String(50), primary_key=True)
    numChequeTransfer = Column(String(150), nullable=False)
    banco = Column(String(100), nullable=False)
    idInstitucion = Column(String(50), ForeignKey('fabribat_cobranzas.InstitucionesFinancieras.idInstitucion'))
    valor = Column(Numeric(18, 2), nullable=False)
    fechaTransaccion = Column(Date)
    usoEmpresa = Column(String(250))
    idComprobante = Column(String(50), ForeignKey('fabribat_cobranzas.Comprobantes.idComprobante', ondelete='CASCADE'), nullable=False)
    createdBy = Column(Text)
    createdOn = Column(DateTime, nullable=False)
    lastModifiedBy = Column(Text)
    lastModifiedOn = Column(DateTime)
    tipo = Column(String(20))
    orden = Column(Integer, nullable=False)

    comprobante = relationship("Comprobante", back_populates="valores_recibidos")


class PagoFactura(Base):
    __tablename__ = 'PagosFacturas'
    __table_args__ = {'schema': 'fabribat_cobranzas'}

    idPagoFactura = Column(String(50), primary_key=True)
    idFactura = Column(String(36))
    factura = Column(String(20), nullable=False)
    facturaOriginal = Column(String(45))
    esCuota = Column(Boolean, default=False)
    tmp_bcSystemId = Column(String(36))
    montoAsignado = Column(Numeric(10, 2), nullable=False)
    idComprobante = Column(String(50), ForeignKey('fabribat_cobranzas.Comprobantes.idComprobante', ondelete='CASCADE'), nullable=False)
    createdBy = Column(Text)
    createdOn = Column(DateTime, nullable=False)
    lastModifiedBy = Column(Text)
    lastModifiedOn = Column(DateTime)
    cuota = Column(Integer)
    abono = Column(Boolean, nullable=False, default=False)
    lastSyncOn = Column(DateTime)
    idCliente = Column(String(36))
    orden = Column(Integer, nullable=False)

    comprobante = relationship("Comprobante", back_populates="pagos_facturas")


class DetallePago(Base):
    __tablename__ = 'DetallePagos'
    __table_args__ = {'schema': 'fabribat_cobranzas'}

    idDetallePago = Column(String(50), primary_key=True)
    idPagoFactura = Column(String(50), nullable=False)
    idValoresRecibidos = Column(String(50), nullable=False)
    idComprobante = Column(String(50), nullable=False)
    idFactura = Column(String(50), nullable=False)
    montoApplied = Column('montoAplicado', Numeric(10, 2), nullable=False)
    origen = Column(String(20), default='MIDDLEWARE')
    createdOn = Column(TIMESTAMP, nullable=False, server_default=func.now())


class Secuencia(Base):
    __tablename__ = 'Secuencias'
    __table_args__ = {'schema': 'fabribat_cobranzas'}

    idSecuencia = Column(String(50), primary_key=True)
    idUsuario = Column(String(255), nullable=False)
    puntoVenta = Column(String(3), nullable=False)
    puntoEmision = Column(String(3), nullable=False)
    secuencial = Column(Integer, nullable=False)
    createdBy = Column(Text)
    createdOn = Column(DateTime, nullable=False)
    lastModifiedBy = Column(Text)
    lastModifiedOn = Column(DateTime)
    date = Column(DateTime)
    idPerfil = Column(String(50))
    tipo = Column(String(20), default='COMPROBANTE')
    prefijo = Column(String(10))


class ChequePosfechadoCab(Base):
    __tablename__ = 'ChequesPosfechadosCab'
    __table_args__ = {'schema': 'fabribat_cobranzas'}

    idChequePosf = Column(String(36), primary_key=True)
    bcChequeId = Column(String(20), nullable=False, unique=True)
    idCliente = Column(String(50))
    numCheque = Column(String(50))
    fechaCheque = Column(Date)
    fechaRegistroCheque = Column(Date)
    regEntryNo = Column(Integer, nullable=False)
    montoTotal = Column(Numeric(18, 2))
    estado = Column(String(20))
    fechaCreacion = Column(DateTime)
    lastSyncOn = Column(DateTime)

    detalles = relationship("ChequePosfechadoDet", back_populates="cabecera", cascade="all, delete-orphan")


class ChequePosfechadoDet(Base):
    __tablename__ = 'ChequesPosfechadosDet'
    __table_args__ = {'schema': 'fabribat_cobranzas'}

    idDetalleChequePosf = Column(String(36), primary_key=True)
    entryNo = Column(Integer, nullable=False)
    idChequePosf = Column(String(36), ForeignKey('fabribat_cobranzas.ChequesPosfechadosCab.idChequePosf'), nullable=False)
    idFactura = Column(String(50))
    montoAplicado = Column(Numeric(18, 2))
    montoRemanente = Column(Numeric(18, 2))
    registrado = Column(Boolean)

    cabecera = relationship("ChequePosfechadoCab", back_populates="detalles")


class FacturaIntiza(Base):
    __tablename__ = 'FacturasIntiza'
    __table_args__ = {'schema': 'fabribat_cobranzas'}

    codigo_cliente = Column(String(50), primary_key=True)
    numero_documento = Column(String(50), primary_key=True)
    Search_Name = Column(String(255))
    fecha = Column(Date)
    descripcion = Column(String(500))
    numero_cheque = Column(String(50))
    estado_cheque = Column(String(50))
    monto = Column(Numeric(15, 2), default=0.00)
    saldo = Column(Numeric(15, 2), default=0.00)
    tipo_documento = Column(String(50))
    termino_pago = Column(String(100))
    monto_cheque = Column(Numeric(15, 2), default=0.00)
    documento_original = Column(String(50))
    monto_Origen = Column(Numeric(15, 2), default=0.00)
    tipo = Column(String(50))