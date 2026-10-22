from datetime import datetime
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

# Único lugar donde se definen los roles válidos (el frontend debe usar los mismos)
RolUsuario = Literal["ADMIN", "CONTABILIDAD", "BASICO", "RECEPTOR_MAIL"]


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
    rol: RolUsuario = "BASICO"


class DestinatarioResponse(BaseModel):
    """Datos mínimos de un usuario activo para elegirlo como destinatario de correo."""
    id_usuario: UUID
    email: str
    nombre: Optional[str] = None
    rol: str


class ActualizarEstadoRequest(BaseModel):
    activo: bool


class ActualizarRolRequest(BaseModel):
    rol: RolUsuario


class ActualizarUsuarioRequest(BaseModel):
    """Edición de un usuario: solo se cambian los campos enviados."""
    email: Optional[EmailStr] = None
    nombre: Optional[str] = Field(None, min_length=1, max_length=150)
    rol: Optional[RolUsuario] = None
    activo: Optional[bool] = None


class SugerenciaUsuarioResponse(BaseModel):
    email: str
    nombre: Optional[str] = None
    # registrado: ya existe en la app | google: Google Workspace | correo: deducido del correo
    origen: Optional[str] = None
    ya_registrado: bool = False
