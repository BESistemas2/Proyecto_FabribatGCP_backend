# app/modulos/identidad/service.py
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional, List
from uuid import UUID
from google.oauth2 import id_token
from google.auth.transport import requests
from fastapi import APIRouter, HTTPException, status, Header, Depends
from sqlalchemy.orm import Session
import jwt

from app.modulos.identidad.repository import IdentidadRepository

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "fabribat_secret_key_change_in_prod")
ALGORITHM = "HS256"
ALLOWED_DOMAIN = os.getenv("ALLOWED_DOMAIN", "bateriasecuador.com")

class AuthService:
    @staticmethod
    async def verify_google_token_and_login(google_token: str, db: Session) -> Dict[str, Any]:
        try:
            # 1. Validar token de Google
            id_info = id_token.verify_oauth2_token(
                google_token, 
                requests.Request()
            )
            
            email = id_info.get("email", "").lower()
            domain = email.split("@")[-1] if "@" in email else ""

            # 2. Validar dominio corporativo
            if ALLOWED_DOMAIN and domain != ALLOWED_DOMAIN:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Acceso restringido a cuentas @{ALLOWED_DOMAIN}"
                )

            # 3. Consultar Allowlist en identidad.usuarios
            repo = IdentidadRepository(db)
            usuario = repo.obtener_usuario_por_email(email)

            if not usuario:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Su cuenta corporativa no tiene permisos para acceder a esta plataforma. Contacte al administrador."
                )

            if not usuario.get("activo"):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Su usuario se encuentra inactivo en el sistema."
                )

            # 4. Generar token JWT de sesión
            expiration = datetime.now(timezone.utc) + timedelta(hours=8)
            payload = {
                "sub": str(usuario["id_usuario"]),
                "email": email,
                "name": usuario.get("nombre") or id_info.get("name"),
                "picture": id_info.get("picture"),
                "rol": usuario.get("rol", "ANALISTA"),
                "exp": expiration
            }
            
            access_token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

            return {
                "access_token": access_token,
                "token_type": "bearer",
                "user": {
                    "id_usuario": str(usuario["id_usuario"]),
                    "email": email,
                    "name": usuario.get("nombre") or id_info.get("name"),
                    "picture": id_info.get("picture"),
                    "rol": usuario.get("rol", "ANALISTA"),
                    "domain": domain
                }
            }
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token de Google inválido o expirado"
            )

    @staticmethod
    def get_user_from_jwt(token: str) -> Dict[str, Any]:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            return {
                "id_usuario": payload.get("sub"),
                "email": payload.get("email"),
                "name": payload.get("name"),
                "picture": payload.get("picture"),
                "rol": payload.get("rol"),
                "domain": payload.get("email", "").split("@")[-1] if payload.get("email") else ""
            }
        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="El token de sesión ha expirado"
            )
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token de sesión inválido"
            )

class UsuarioAdminService:
    def __init__(self, db: Session):
        self.repo = IdentidadRepository(db)

    def listar_todos(self) -> List[Dict[str, Any]]:
        return self.repo.listar_usuarios()

    def registrar_usuario(self, email: str, nombre: str, rol: str) -> Dict[str, Any]:
        email_clean = email.lower().strip()
        if not email_clean.endswith("@bateriasecuador.com"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Solo se permiten correos corporativos @bateriasecuador.com"
            )

        existente = self.repo.obtener_usuario_por_email(email_clean)
        if existente:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El correo ya se encuentra registrado en el sistema."
            )

        return self.repo.crear_usuario(email_clean, nombre, rol)

    def cambiar_estado(self, id_usuario: UUID, activo: bool) -> Dict[str, Any]:
        usuario = self.repo.actualizar_estado(id_usuario, activo)
        if not usuario:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Usuario no encontrado."
            )
        return usuario

    def cambiar_rol(self, id_usuario: UUID, rol: str) -> Dict[str, Any]:
        usuario = self.repo.actualizar_rol(id_usuario, rol)
        if not usuario:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Usuario no encontrado."
            )
        return usuario