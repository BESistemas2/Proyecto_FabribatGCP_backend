import re
from datetime import date
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class EjecutarConciliacionPayload(BaseModel):
    id_cuenta: UUID
    periodo: str = Field(..., description="AAAA-MM", examples=["2026-08"])
    fecha_inicio: date
    fecha_fin: date
    # Se aceptan por compatibilidad con el frontend, pero se ignoran:
    # el usuario sale del token y la cuenta contable de la cuenta bancaria.
    creado_por: Optional[str] = None
    cuenta_contable: Optional[str] = None

    @field_validator("periodo")
    @classmethod
    def _periodo(cls, v: str) -> str:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", v or ""):
            raise ValueError("El periodo debe tener el formato AAAA-MM")
        return v

    @model_validator(mode="after")
    def _rango(self):
        if self.fecha_inicio > self.fecha_fin:
            raise ValueError("fecha_inicio no puede ser posterior a fecha_fin")
        return self


class EnviarReporteEmailPayload(BaseModel):
    destinatarios: List[EmailStr] = Field(..., min_length=1, max_length=20)
    comentario: Optional[str] = Field(None, max_length=2000)


class PartidaTransitoResponse(BaseModel):
    id: str
    fecha: str
    referencia: str
    concepto: str
    monto: float
    tipo: str
    estado: Optional[str] = None
    regla_cruce: Optional[str] = None
    # Solo partidas del ERP (BC)
    documento: Optional[str] = None
    entry_no: Optional[int] = None


class ActaResponse(BaseModel):
    id_acta: UUID
    id_cuenta: Optional[UUID] = None
    periodo: str
    fecha_inicio: Optional[date] = None
    fecha_fin: Optional[date] = None
    cuenta_contable: str
    banco: Optional[str] = None
    saldo_banco: float
    saldo_libros: float
    total_conciliado: float
    transitos_banco: int = 0
    transitos_erp: int = 0
    total_partidas_en_transito: int = 0
    estado: str
    creado_por: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ActaDetalleResponse(BaseModel):
    acta: ActaResponse
    partidas_transito_banco: List[PartidaTransitoResponse]
    partidas_transito_erp: List[PartidaTransitoResponse]
