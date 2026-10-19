from datetime import date, datetime, time
from decimal import Decimal
from typing import Optional, List, Union
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class InstitucionBase(BaseModel):
    id_institucion: Union[UUID, str]
    nombre: str
    codigo: Optional[int] = None
    permite_depositos: bool = False
    emite_cheques: bool = False

    model_config = ConfigDict(from_attributes=True)


class CuentaBancariaBase(BaseModel):
    codigo_banco_bc: str
    nombre_cuenta: str
    numero_cuenta: Optional[str] = None
    grupo_registro_bc: Optional[str] = None
    cuenta_contable_bc: Optional[str] = None
    moneda: str = "USD"
    id_institucion: Optional[Union[UUID, str]] = None
    rpa_habilitado: bool = True
    rpa_frecuencia: str = "DIARIA"
    rpa_intervalo_minutos: Optional[int] = 1440
    rpa_hora_ejecucion: Optional[time] = None
    auto_match_habilitado: bool = True
    auto_match_frecuencia: str = "DIARIA"
    auto_match_intervalo_minutos: Optional[int] = 1440
    auto_match_hora: Optional[time] = None
    activo: bool = True


class CuentaBancariaUpdate(BaseModel):
    id_institucion: Optional[Union[UUID, str]] = None
    rpa_habilitado: Optional[bool] = None
    rpa_frecuencia: Optional[str] = None
    rpa_intervalo_minutos: Optional[int] = None
    rpa_hora_ejecucion: Optional[time] = None
    auto_match_habilitado: Optional[bool] = None
    auto_match_frecuencia: Optional[str] = None
    auto_match_intervalo_minutos: Optional[int] = None
    auto_match_hora: Optional[time] = None
    activo: Optional[bool] = None


class CuentaBancariaResponse(CuentaBancariaBase):
    id_cuenta: UUID
    alias: Optional[str] = None
    banco_nombre: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class CargaExtractoCreate(BaseModel):
    id_cuenta: UUID
    nombre_archivo: str
    created_by: str
    id_institucion: Optional[Union[UUID, str]] = None


class CargaExtractoResponse(BaseModel):
    id_carga: UUID
    id_cuenta: Optional[UUID] = None
    id_institucion: Optional[Union[UUID, str]] = None
    nombre_archivo: str
    url_archivo: Optional[str] = None
    total_registros_leidos: Optional[int] = 0
    registros_importados: Optional[int] = 0
    estado: Optional[str] = None
    mensaje_log: Optional[str] = None
    hash_archivo: Optional[str] = None
    created_by: Optional[str] = None
    banco_detectado: Optional[str] = None
    fecha_desde: Optional[date] = None
    fecha_hasta: Optional[date] = None
    saldo_inicial: Optional[Decimal] = None
    saldo_final: Optional[Decimal] = None
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class MovimientoResponse(BaseModel):
    id_movimiento: UUID
    id_carga: UUID
    id_cuenta: UUID
    id_institucion: Optional[Union[UUID, str]] = None
    fecha_transaccion: date
    numero_referencia: str
    concepto: Optional[str] = None
    monto: Decimal  # con signo: CREDITO > 0, DEBITO < 0
    tipo_movimiento: Optional[str] = None
    estado: str
    info_adicional: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class CargaUploadResponse(BaseModel):
    status: str
    id_carga: UUID
    mensaje: str
    banco: Optional[str] = None
    registros_leidos: int = 0
    registros_importados: int = 0
    saldo_inicial: Optional[Decimal] = None
    saldo_final: Optional[Decimal] = None
    advertencias: List[str] = []
