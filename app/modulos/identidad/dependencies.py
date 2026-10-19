from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db_pg
from app.modulos.identidad.repository import IdentidadRepository
from app.modulos.identidad.service import AuthService

security = HTTPBearer(auto_error=False)


def obtener_usuario_autenticado(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db_pg),
) -> dict:
    """
    Valida el JWT y confirma en la BD que el usuario sigue activo.
    El rol se toma de la BD (no del token): un cambio de rol o una
    desactivación aplican de inmediato, sin esperar a que expire el token.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token no proporcionado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    usuario = AuthService.get_user_from_jwt(credentials.credentials)
    try:
        id_usuario = UUID(str(usuario["id_usuario"]))
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token de sesión inválido",
                            headers={"WWW-Authenticate": "Bearer"})

    actual = IdentidadRepository(db).obtener_usuario_por_id(id_usuario)
    if not actual or not actual.get("activo"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Su usuario ya no tiene acceso a la plataforma.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    usuario["rol"] = actual.get("rol")
    usuario["name"] = actual.get("nombre") or usuario.get("name")
    return usuario


def requerir_rol(*roles: str):
    """Dependencia que exige alguno de los roles indicados."""
    def _verificar(usuario_actual: dict = Depends(obtener_usuario_autenticado)) -> dict:
        if usuario_actual.get("rol") not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No tiene permisos para realizar esta acción.",
            )
        return usuario_actual
    return _verificar


def requerir_rol_admin(usuario_actual: dict = Depends(obtener_usuario_autenticado)) -> dict:
    if usuario_actual.get("rol") != "ADMIN":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acceso denegado: se requieren privilegios de Administrador.",
        )
    return usuario_actual
