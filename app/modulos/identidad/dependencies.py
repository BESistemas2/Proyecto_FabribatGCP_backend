from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.modulos.identidad.service import AuthService

security = HTTPBearer()

def obtener_usuario_autenticado(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    token = credentials.credentials
    return AuthService.get_user_from_jwt(token)

def requerir_rol_admin(
    usuario_actual: dict = Depends(obtener_usuario_autenticado)
) -> dict:
    if usuario_actual.get("rol") != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado: Se requieren privilegios de Administrador."
        )
    return usuario_actual