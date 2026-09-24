from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List, Union
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class PartidaTransitoBase(BaseModel):
    origen_dato: str
    fecha_transaccion: date
    documento_referencia: Optional[str] = None
    descripcion: Optional[str] = None
    monto: Decimal


class PartidaTransitoCreate(PartidaTransitoBase):
    pass


class PartidaTransitoResponse(PartidaTransitoBase):
    id_partida: int
    id_acta: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ActaCreate(BaseModel):
    id_cuenta: UUID
    cuenta_contable: str
    periodo: str
    saldo_banco: Decimal
    saldo_libros: Decimal
    creado_por: str


class ActaResponse(BaseModel):
    id_acta: UUID
    id_cuenta: Optional[UUID] = None
    cuenta_contable: str
    periodo: str
    saldo_banco: Decimal
    saldo_libros: Decimal
    total_conciliado: Decimal
    creado_por: str
    estado: str
    created_at: datetime
    updated_at: datetime
    partidas: List[PartidaTransitoResponse] = []

    model_config = ConfigDict(from_attributes=True)


class EjecutarConciliacionRequest(BaseModel):
    id_cuenta: UUID
    periodo: str
    creado_por: str
    cuenta_contable: Optional[str] = None