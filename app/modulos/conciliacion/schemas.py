import re
from datetime import date
from decimal import Decimal
from typing import List, Literal, Optional
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


# --- Corte inicial por cuenta ------------------------------------------------

CategoriaPartida = Literal["CHEQUE_NO_COBRADO", "NOTA_DEBITO_NO_REGISTRADA", "NOTA_CREDITO_NO_REGISTRADA",
                           "PENDIENTE_ACREDITAR", "PENDIENTE_DEBITAR"]


class PartidaInicialIn(BaseModel):
    categoria: CategoriaPartida
    fecha: date
    detalle: str = Field(..., min_length=1, max_length=255)
    referencia: Optional[str] = Field(None, max_length=100)
    valor: Decimal = Field(..., gt=0, max_digits=18, decimal_places=2)
    observacion: Optional[str] = Field(None, max_length=255)


class CorteInicialIn(BaseModel):
    fecha_corte: date
    saldo_banco: Decimal = Field(..., max_digits=18, decimal_places=2)
    saldo_libros: Decimal = Field(..., max_digits=18, decimal_places=2)
    observacion: Optional[str] = Field(None, max_length=500)
    partidas: List[PartidaInicialIn] = Field(default_factory=list, max_length=2000)


class PartidaInicialOut(PartidaInicialIn):
    id_partida: int
    estado: str


class CorteInicialOut(BaseModel):
    id_corte: UUID
    id_cuenta: UUID
    fecha_corte: date
    saldo_banco: Decimal
    saldo_libros: Decimal
    observacion: Optional[str] = None
    partidas: List[PartidaInicialOut]
    saldo_libros_calculado: Decimal
    diferencia: Decimal
    bloqueado: bool
    motivo_bloqueo: Optional[str] = None
    creado_por: str
    actualizado_por: Optional[str] = None
    updated_at: Optional[str] = None


# --- Gastos bancarios (migración 009) ------------------------------------------
class PatronGastoIn(BaseModel):
    banco: str = Field(..., min_length=2, max_length=50, description="Texto contenido en el nombre del banco")
    patron: str = Field(..., min_length=2, max_length=150)
    tipo_coincidencia: Literal["EMPIEZA", "CONTIENE", "REGEX"] = "EMPIEZA"
    categoria: str = Field("Comisión", min_length=2, max_length=30)
    descripcion: Optional[str] = Field(None, max_length=150)
    activo: bool = True


class PatronGastoOut(PatronGastoIn):
    id_patron: int
    creado_por: str

    model_config = {"from_attributes": True}
