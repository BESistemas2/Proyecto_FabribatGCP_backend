# app/modulos/identidad/service.py
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

import jwt
from fastapi import HTTPException, status
from google.auth.exceptions import TransportError
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import (
    ALLOWED_DOMAIN,
    GOOGLE_CLIENT_ID,
    JWT_ALGORITHM,
    JWT_EXPIRE_HOURS,
    JWT_ISSUER,
    JWT_SECRET_KEY,
)
from app.modulos.auditoria.servicio import diferencias, registrar_evento, registrar_evento_aparte
from app.modulos.identidad import directorio
from app.modulos.identidad.repository import IdentidadRepository

logger = logging.getLogger(__name__)

# Compatibilidad con código que importaba estas constantes desde aquí
SECRET_KEY = JWT_SECRET_KEY
ALGORITHM = JWT_ALGORITHM

_google_request = google_requests.Request()


def _rechazo(exc: HTTPException, motivo: str, email: str = "") -> HTTPException:
    """Registra el inicio de sesión rechazado (en su propia transacción) y devuelve la excepción."""
    registrar_evento_aparte(
        "identidad.sesion.rechazar", f"Inicio de sesión rechazado{' para ' + email if email else ''}: {motivo}",
        entidad="usuario", resultado="DENEGADO", usuario={"email": email or None},
        datos={"motivo": motivo})
    return exc


def _no_autorizado(detalle: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detalle,
        headers={"WWW-Authenticate": "Bearer"},
    )


class AuthService:
    @staticmethod
    def verify_google_token_and_login(google_token: str, db: Session) -> Dict[str, Any]:
        # 1. Validar el ID token de Google: firma, expiración y que fue emitido para ESTA app
        try:
            id_info = id_token.verify_oauth2_token(google_token, _google_request, audience=GOOGLE_CLIENT_ID)
        except ValueError:
            raise _rechazo(_no_autorizado("Token de Google inválido o expirado"), "token de Google inválido o expirado")
        except TransportError:
            logger.exception("No se pudieron descargar los certificados de Google")
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                "No se pudo validar el inicio de sesión con Google. Intente nuevamente.")

        email = str(id_info.get("email") or "").lower()
        domain = email.split("@")[-1] if "@" in email else ""

        # 2. Cuenta corporativa verificada (hd = dominio de Google Workspace)
        if not id_info.get("email_verified"):
            raise _rechazo(HTTPException(status.HTTP_403_FORBIDDEN, "El correo de Google no está verificado."),
                           "correo no verificado", email)
        if ALLOWED_DOMAIN and (domain != ALLOWED_DOMAIN or id_info.get("hd") != ALLOWED_DOMAIN):
            raise _rechazo(HTTPException(status.HTTP_403_FORBIDDEN, f"Acceso restringido a cuentas @{ALLOWED_DOMAIN}"),
                           "dominio no permitido", email)

        # 3. Allowlist en identidad.usuarios
        usuario = IdentidadRepository(db).obtener_usuario_por_email(email)
        if not usuario:
            raise _rechazo(HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Su cuenta corporativa no tiene permisos para acceder a esta plataforma. "
                "Contacte al administrador.",
            ), "no está registrado", email)
        if not usuario.get("activo"):
            raise _rechazo(HTTPException(status.HTTP_403_FORBIDDEN, "Su usuario se encuentra inactivo en el sistema."),
                           "usuario inactivo", email)
        if usuario.get("rol") == "RECEPTOR_MAIL":
            raise _rechazo(HTTPException(status.HTTP_403_FORBIDDEN,
                                         "Su rol solo recibe reportes por correo; no tiene acceso a la plataforma."),
                           "rol RECEPTOR_MAIL sin acceso", email)

        # 4. JWT de sesión
        ahora = datetime.now(timezone.utc)
        nombre = usuario.get("nombre") or id_info.get("name")
        payload = {
            "sub": str(usuario["id_usuario"]),
            "email": email,
            "name": nombre,
            "picture": id_info.get("picture"),
            "rol": usuario.get("rol"),
            "iat": ahora,
            "exp": ahora + timedelta(hours=JWT_EXPIRE_HOURS),
            "iss": JWT_ISSUER,
        }
        registrar_evento(db, "identidad.sesion.iniciar", f"Inicio de sesión de {email}",
                         entidad="usuario", id_entidad=usuario["id_usuario"],
                         usuario={"id_usuario": usuario["id_usuario"], "email": email, "rol": usuario.get("rol")})
        db.commit()
        logger.info("Inicio de sesión: %s", email)
        return {
            "access_token": jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM),
            "token_type": "bearer",
            "expires_in": JWT_EXPIRE_HOURS * 3600,
            "user": {
                "id_usuario": str(usuario["id_usuario"]),
                "email": email,
                "name": nombre,
                "picture": id_info.get("picture"),
                "rol": usuario.get("rol"),
                "domain": domain,
            },
        }

    @staticmethod
    def decode_jwt(token: str) -> Dict[str, Any]:
        """Valida firma, expiración y emisor del JWT de sesión."""
        try:
            return jwt.decode(
                token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM],
                issuer=JWT_ISSUER, options={"require": ["exp", "sub"]},
            )
        except jwt.ExpiredSignatureError:
            raise _no_autorizado("El token de sesión ha expirado")
        except jwt.PyJWTError:
            raise _no_autorizado("Token de sesión inválido")

    @staticmethod
    def get_user_from_jwt(token: str) -> Dict[str, Any]:
        """Datos del usuario tal como vienen en el token (sin consultar la BD)."""
        payload = AuthService.decode_jwt(token)
        email = payload.get("email") or ""
        return {
            "id_usuario": payload.get("sub"),
            "email": email,
            "name": payload.get("name"),
            "picture": payload.get("picture"),
            "rol": payload.get("rol"),
            "domain": email.split("@")[-1] if email else "",
        }


class UsuarioAdminService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = IdentidadRepository(db)

    def _auditar_y_confirmar(self, accion: str, resumen: str, usuario: Dict[str, Any],
                             cambios: Optional[Dict[str, Any]] = None) -> None:
        registrar_evento(self.db, accion, resumen, entidad="usuario", id_entidad=usuario["id_usuario"],
                         cambios=cambios)
        self.db.commit()

    def listar_todos(self) -> List[Dict[str, Any]]:
        return self.repo.listar_usuarios()

    def registrar_usuario(self, email: str, nombre: str, rol: str) -> Dict[str, Any]:
        email_clean = email.lower().strip()
        if not email_clean.endswith(f"@{ALLOWED_DOMAIN}"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"Solo se permiten correos corporativos @{ALLOWED_DOMAIN}")
        if self.repo.obtener_usuario_por_email(email_clean):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "El correo ya se encuentra registrado en el sistema.")
        try:
            usuario = self.repo.crear_usuario(email_clean, nombre.strip(), rol)
            self._auditar_y_confirmar("identidad.usuario.crear", f"Creó el usuario {email_clean} con rol {rol}",
                                      usuario, {"email": [None, email_clean], "nombre": [None, usuario["nombre"]],
                                                "rol": [None, rol], "activo": [None, True]})
        except IntegrityError:
            self.repo.db.rollback()
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "No se pudo crear el usuario (correo duplicado o rol inválido).")
        return usuario

    def cambiar_estado(self, id_usuario: UUID, activo: bool, admin: Dict[str, Any]) -> Dict[str, Any]:
        if not activo and str(id_usuario) == str(admin.get("id_usuario")):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "No puedes desactivar tu propio usuario.")
        antes = self.repo.obtener_usuario_por_id(id_usuario)
        usuario = self.repo.actualizar_estado(id_usuario, activo)
        if not usuario:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado.")
        self._auditar_y_confirmar("identidad.usuario.editar",
                                  f"{'Activó' if activo else 'Desactivó'} al usuario {usuario['email']}",
                                  usuario, diferencias(antes or {}, usuario, omitir=("created_at",)))
        logger.info("%s cambió el estado de %s a activo=%s", admin.get("email"), usuario["email"], activo)
        return usuario

    def cambiar_rol(self, id_usuario: UUID, rol: str, admin: Dict[str, Any]) -> Dict[str, Any]:
        if rol != "ADMIN" and str(id_usuario) == str(admin.get("id_usuario")):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "No puedes quitarte a ti mismo el rol de administrador.")
        antes = self.repo.obtener_usuario_por_id(id_usuario)
        try:
            usuario = self.repo.actualizar_rol(id_usuario, rol)
        except IntegrityError:
            self.repo.db.rollback()
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Rol inválido.")
        if not usuario:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado.")
        self._auditar_y_confirmar("identidad.usuario.editar", f"Cambió el rol de {usuario['email']} a {rol}",
                                  usuario, diferencias(antes or {}, usuario, omitir=("created_at",)))
        logger.info("%s cambió el rol de %s a %s", admin.get("email"), usuario["email"], rol)
        return usuario


    def actualizar_usuario(self, id_usuario: UUID, cambios: Dict[str, Any], admin: Dict[str, Any]) -> Dict[str, Any]:
        """Edición completa desde la pantalla de administración (mismas reglas que rol/estado)."""
        actual = self.repo.obtener_usuario_por_id(id_usuario)
        if not actual:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado.")
        es_yo = str(id_usuario) == str(admin.get("id_usuario"))
        if "email" in cambios:
            cambios["email"] = cambios["email"].lower().strip()
            if not cambios["email"].endswith(f"@{ALLOWED_DOMAIN}"):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Solo se permiten correos corporativos @{ALLOWED_DOMAIN}")
            otro = self.repo.obtener_usuario_por_email(cambios["email"])
            if otro and str(otro["id_usuario"]) != str(id_usuario):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "El correo ya está registrado para otro usuario.")
            if es_yo and cambios["email"] != actual["email"]:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "No puedes cambiar tu propio correo (cerrarías tu sesión).")
        if "nombre" in cambios:
            cambios["nombre"] = cambios["nombre"].strip()
        if es_yo and cambios.get("rol") not in (None, "ADMIN"):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "No puedes quitarte a ti mismo el rol de administrador.")
        if es_yo and cambios.get("activo") is False:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "No puedes desactivar tu propio usuario.")
        try:
            usuario = self.repo.actualizar_usuario(id_usuario, cambios)
            dif = diferencias(actual, usuario, omitir=("created_at",))
            if dif:
                self._auditar_y_confirmar("identidad.usuario.editar",
                                          f"Editó al usuario {usuario['email']}: {', '.join(sorted(dif))}",
                                          usuario, dif)
            else:
                self.db.commit()
        except IntegrityError:
            self.repo.db.rollback()
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "No se pudo actualizar el usuario (correo duplicado o rol inválido).")
        logger.info("%s actualizó a %s: %s", admin.get("email"), usuario["email"], sorted(cambios))
        return usuario

    def sugerir_usuario(self, email: str) -> Dict[str, Any]:
        """Nombre sugerido para un correo: registro existente > Google Workspace > deducido del correo."""
        email = (email or "").lower().strip()
        existente = self.repo.obtener_usuario_por_email(email) if email else None
        if existente:
            return {"email": email, "nombre": existente.get("nombre"), "origen": "registrado", "ya_registrado": True}
        nombre = directorio.nombre_en_google(email)
        if nombre:
            return {"email": email, "nombre": nombre, "origen": "google", "ya_registrado": False}
        nombre = directorio.nombre_desde_correo(email)
        return {"email": email, "nombre": nombre, "origen": "correo" if nombre else None, "ya_registrado": False}
