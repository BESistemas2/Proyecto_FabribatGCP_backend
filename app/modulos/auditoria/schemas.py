# app/modulos/auditoria/schemas.py
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel


class EventoOut(BaseModel):
    id_evento: int
    ocurrido_en: datetime
    modulo: str
    accion: str
    nivel: str
    resultado: str
    entidad: Optional[str] = None
    id_entidad: Optional[str] = None
    id_entidad_padre: Optional[str] = None
    id_usuario: Optional[UUID] = None
    email: Optional[str] = None
    rol: Optional[str] = None
    origen: str
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    id_solicitud: Optional[str] = None
    resumen: str
    cambios: Optional[Dict[str, Any]] = None
    datos: Optional[Dict[str, Any]] = None


class PaginaEventos(BaseModel):
    total: int
    eventos: List[EventoOut]


class AccionOut(BaseModel):
    codigo: str
    modulo: str
    descripcion: str
    nivel: str
    activo: bool


class VerificacionOut(BaseModel):
    eventos_revisados: int
    correcta: bool
    id_evento_con_error: Optional[int] = None
    detalle: str
