from datetime import date, datetime, time
from decimal import Decimal
from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel, ConfigDict
from pydantic import BaseModel, Field


class InstitucionBase(BaseModel):
    id_institucion: str
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
    id_institucion: Optional[str] = None
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
    id_institucion: Optional[str] = None
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
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CargaExtractoCreate(BaseModel):
    id_cuenta: UUID
    nombre_archivo: str
    created_by: str
    id_institucion: Optional[str] = None


class CargaExtractoResponse(BaseModel):
    id_carga: UUID
    id_cuenta: Optional[UUID] = None
    id_institucion: Optional[str] = None
    nombre_archivo: str
    url_archivo: Optional[str] = None
    total_registros_leidos: int
    registros_importados: int
    estado: str
    mensaje_log: Optional[str] = None
    hash_archivo: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MovimientoResponse(BaseModel):
    id_movimiento: UUID
    id_carga: UUID
    id_cuenta: UUID
    id_institucion: Optional[str] = None
    fecha_transaccion: date
    numero_referencia: str
    concepto: Optional[str] = None
    monto: Decimal
    tipo_movimiento: Optional[str] = None
    estado: str
    info_adicional: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class CargaAppSheetRequest(BaseModel):
    id_cuenta: UUID = Field(..., description="ID de la cuenta bancaria en PostgreSQL")
    rutaArchivo: str = Field(..., description="ID de archivo de Google Drive, URL de Drive o nombre de archivo")
    usuarioCarga: str = Field(default="appsheet_user", description="Usuario que realiza la carga desde AppSheet")