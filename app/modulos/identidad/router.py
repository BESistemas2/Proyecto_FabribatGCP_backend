from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db_pg
from app.modulos.identidad.dependencies import obtener_usuario_autenticado, requerir_rol_admin
from app.modulos.identidad.schemas import (
    ActualizarEstadoRequest,
    ActualizarRolRequest,
    CrearUsuarioRequest,
    UsuarioResponse,
)
from app.modulos.identidad.service import AuthService, UsuarioAdminService

router = APIRouter(prefix="/api/v1/identidad", tags=["Autenticación"])


class GoogleLoginRequest(BaseModel):
    token: Optional[str] = None
    credential: Optional[str] = None
    id_token: Optional[str] = None


# Endpoints síncronos: la verificación con Google y la BD corren en un hilo
@router.post("/auth/google")
def login_google(req: GoogleLoginRequest, db: Session = Depends(get_db_pg)):
    raw_token = req.credential or req.token or req.id_token
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Se requiere el ID Token de Google")
    return AuthService.verify_google_token_and_login(raw_token, db)


@router.get("/auth/me")
def get_current_user(usuario: dict = Depends(obtener_usuario_autenticado)):
    return usuario


# --- ADMINISTRACIÓN DE USUARIOS (ALLOWLIST) ---

@router.get("/usuarios", response_model=List[UsuarioResponse])
def listar_usuarios(db: Session = Depends(get_db_pg), admin: dict = Depends(requerir_rol_admin)):
    return UsuarioAdminService(db).listar_todos()


@router.post("/usuarios", response_model=UsuarioResponse, status_code=status.HTTP_201_CREATED)
def crear_usuario(payload: CrearUsuarioRequest, db: Session = Depends(get_db_pg),
                  admin: dict = Depends(requerir_rol_admin)):
    return UsuarioAdminService(db).registrar_usuario(payload.email, payload.nombre, payload.rol)


@router.patch("/usuarios/{id_usuario}/estado", response_model=UsuarioResponse)
def actualizar_estado_usuario(id_usuario: UUID, payload: ActualizarEstadoRequest,
                              db: Session = Depends(get_db_pg), admin: dict = Depends(requerir_rol_admin)):
    return UsuarioAdminService(db).cambiar_estado(id_usuario, payload.activo, admin)


@router.put("/usuarios/{id_usuario}", response_model=UsuarioResponse)
def actualizar_rol_usuario(id_usuario: UUID, payload: ActualizarRolRequest,
                           db: Session = Depends(get_db_pg), admin: dict = Depends(requerir_rol_admin)):
    return UsuarioAdminService(db).cambiar_rol(id_usuario, payload.rol, admin)
