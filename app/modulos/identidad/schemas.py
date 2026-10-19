from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

# Único lugar donde se definen los roles válidos (el frontend debe usar los mismos)
RolUsuario = Literal["ADMIN", "ANALISTA", "TESORERO"]


class UsuarioResponse(BaseModel):
    id_usuario: UUID
    email: str
    nombre: Optional[str] = None
    rol: str
    activo: bool
    created_at: Optional[datetime] = None


class CrearUsuarioRequest(BaseModel):
    email: EmailStr
    nombre: str = Field(..., min_length=1, max_length=150)
    rol: RolUsuario = "ANALISTA"


class ActualizarEstadoRequest(BaseModel):
    activo: bool


class ActualizarRolRequest(BaseModel):
    rol: RolUsuario
