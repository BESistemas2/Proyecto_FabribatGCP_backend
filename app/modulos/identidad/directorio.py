# app/modulos/identidad/directorio.py
"""
Nombre sugerido para un correo corporativo al crear usuarios.

1. Google Workspace (opcional): Directory API, vista pública del dominio. Requiere
   GOOGLE_DIRECTORY_CREDENTIALS (JSON de cuenta de servicio con delegación de dominio,
   scope admin.directory.user.readonly) y GOOGLE_DIRECTORY_SUBJECT (un usuario del dominio).
2. Si no está configurado o falla: se deduce del correo ("juan.perez" -> "Juan Perez").
"""
import logging
import re
import time
from functools import lru_cache
from typing import Dict, Optional, Tuple
from urllib.parse import quote

from app.core.config import GOOGLE_DIRECTORY_CREDENTIALS, GOOGLE_DIRECTORY_SUBJECT

logger = logging.getLogger(__name__)

SCOPE = "https://www.googleapis.com/auth/admin.directory.user.readonly"
_CACHE_SEG = 600
_cache: Dict[str, Tuple[float, Optional[str]]] = {}


def habilitado() -> bool:
    return bool(GOOGLE_DIRECTORY_CREDENTIALS and GOOGLE_DIRECTORY_SUBJECT)


@lru_cache(maxsize=1)
def _sesion():
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account
    cred = service_account.Credentials.from_service_account_file(
        GOOGLE_DIRECTORY_CREDENTIALS, scopes=[SCOPE], subject=GOOGLE_DIRECTORY_SUBJECT)
    return AuthorizedSession(cred)


def nombre_en_google(email: str) -> Optional[str]:
    if not habilitado() or not email:
        return None
    ahora = time.monotonic()
    if email in _cache and ahora - _cache[email][0] < _CACHE_SEG:
        return _cache[email][1]
    nombre = None
    try:
        r = _sesion().get(
            f"https://admin.googleapis.com/admin/directory/v1/users/{quote(email)}",
            params={"viewType": "domain_public", "fields": "name"}, timeout=5)
        if r.status_code == 200:
            nombre = (r.json().get("name") or {}).get("fullName") or None
        elif r.status_code != 404:
            logger.warning("Directorio de Google respondió %s para %s", r.status_code, email)
    except Exception as exc:  # noqa: BLE001 - la sugerencia nunca debe romper el alta
        logger.warning("No se pudo consultar el directorio de Google: %s", exc)
    _cache[email] = (ahora, nombre)
    return nombre


def nombre_desde_correo(email: str) -> Optional[str]:
    """'analista-administrativo1@...' -> 'Analista Administrativo'."""
    local = (email or "").split("@")[0]
    partes = [re.sub(r"\d+", "", p) for p in re.split(r"[._\-+]+", local)]
    partes = [p for p in partes if len(p) > 1]
    return " ".join(p.capitalize() for p in partes) or None
