from pydantic import BaseModel, EmailStr
from typing import Optional, Literal
from uuid import UUID
from datetime import datetime

class UsuarioResponse(BaseModel):
    id_usuario: UUID
    email: str
    nombre: Optional[str] = None
    rol: str
    activo: bool
    created_at: Optional[datetime] = None

class CrearUsuarioRequest(BaseModel):
    email: EmailStr
    nombre: str
    rol: Literal["ADMIN", "ANALISTA", "TESORERO"] = "ANALISTA"

class ActualizarEstadoRequest(BaseModel):
    activo: bool

class ActualizarRolRequest(BaseModel):
    rol: Literal["ADMIN", "ANALISTA", "TESORERO"]