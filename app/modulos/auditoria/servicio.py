# app/modulos/auditoria/servicio.py
"""
Registro de eventos de auditoría.

Contrato para todos los módulos (ver AGENTS.md):

    registrar_evento(db, "conciliacion.acta.cerrar", "Cerró el acta de 2026-10 (B030)",
                     entidad="acta", id_entidad=acta.id_acta, datos={...})
    db.commit()

- Se llama ANTES del commit del servicio, en la misma sesión: si la operación se revierte,
  el evento también; si el evento falla, la operación no se guarda.
- La acción debe existir en auditoria.acciones (se agrega con la migración del módulo).
- Quién, desde dónde y el id de solicitud salen del contexto (app/core/contexto.py).
- registrar_evento_aparte(...) guarda en su propia transacción: para accesos negados,
  inicios de sesión rechazados y errores, que deben quedar aunque la operación falle.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Iterable, Mapping, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import contexto

logger = logging.getLogger(__name__)

RESULTADOS = ("OK", "ERROR", "DENEGADO")
OCULTO = "***"
# Nunca se guardan: se reemplazan por *** en cambios y datos
_CLAVE_SENSIBLE = re.compile(r"pass|secret|token|clave|credencial|mfa|api_key|authorization", re.IGNORECASE)

_SQL_INSERTAR = text("""
    INSERT INTO auditoria.eventos
        (accion, resultado, entidad, id_entidad, id_entidad_padre, id_usuario, email, rol, origen,
         ip, user_agent, id_solicitud, resumen, cambios, datos)
    VALUES
        (:accion, :resultado, :entidad, :id_entidad, :id_entidad_padre, CAST(:id_usuario AS uuid), :email, :rol,
         :origen, CAST(:ip AS inet), :user_agent, :id_solicitud, :resumen,
         CAST(:cambios AS jsonb), CAST(:datos AS jsonb))
""")

# Mientras no se ejecute la migración 011 la aplicación sigue funcionando sin auditar
# (el arranque avisa que falta). Se vuelve a comprobar cada minuto.
_disponible: Dict[str, Any] = {"valor": None, "revisado": 0.0}
_REVISION_SEG = 60


def auditoria_disponible(db: Session) -> bool:
    ahora = time.monotonic()
    if _disponible["valor"] is True:
        return True
    if _disponible["valor"] is False and ahora - _disponible["revisado"] < _REVISION_SEG:
        return False
    valor = db.execute(text("SELECT to_regclass('auditoria.eventos') IS NOT NULL")).scalar()
    _disponible.update(valor=bool(valor), revisado=ahora)
    if not valor:
        logger.warning("Auditoría no instalada (falta la migración 011): el evento no se registra")
    return bool(valor)


def _a_json(valor: Any) -> Any:
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, (UUID, Enum)):
        return str(valor.value if isinstance(valor, Enum) else valor)
    if isinstance(valor, (set, frozenset, tuple)):
        return list(valor)
    return str(valor)


def ocultar_sensibles(valor: Any) -> Any:
    """Reemplaza por *** los valores cuyas claves parecen secretos (recursivo)."""
    if isinstance(valor, Mapping):
        return {k: (OCULTO if _CLAVE_SENSIBLE.search(str(k)) else ocultar_sensibles(v)) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [ocultar_sensibles(v) for v in valor]
    return valor


def _json(valor: Optional[Mapping[str, Any]]) -> Optional[str]:
    if not valor:
        return None
    return json.dumps(ocultar_sensibles(valor), ensure_ascii=False, default=_a_json, sort_keys=True)


def diferencias(antes: Mapping[str, Any], despues: Mapping[str, Any],
                omitir: Iterable[str] = ()) -> Dict[str, list]:
    """{campo: [antes, después]} solo de los campos que cambiaron."""
    omitir = set(omitir)
    cambios = {}
    for campo in sorted(set(antes) | set(despues)):
        if campo in omitir:
            continue
        a, d = antes.get(campo), despues.get(campo)
        if _normal(a) != _normal(d):
            cambios[campo] = [a, d]
    return cambios


def _normal(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool, type(None))):
        return v
    if isinstance(v, Decimal):
        return v.normalize()
    if isinstance(v, (list, tuple, set)):
        return sorted(_a_json(x) for x in v)
    return _a_json(v)


def registrar_evento(
    db: Session,
    accion: str,
    resumen: str,
    *,
    entidad: Optional[str] = None,
    id_entidad: Any = None,
    id_entidad_padre: Any = None,
    cambios: Optional[Mapping[str, Any]] = None,
    datos: Optional[Mapping[str, Any]] = None,
    resultado: str = "OK",
    usuario: Optional[Mapping[str, Any]] = None,
) -> None:
    """Agrega el evento a la transacción de `db` (no hace commit)."""
    if resultado not in RESULTADOS:
        raise ValueError(f"Resultado de auditoría inválido: {resultado}")
    if not auditoria_disponible(db):
        return
    ctx = contexto.actual()
    usuario = usuario or {}
    db.execute(_SQL_INSERTAR, {
        "accion": accion,
        "resultado": resultado,
        "entidad": entidad,
        "id_entidad": None if id_entidad is None else str(id_entidad),
        "id_entidad_padre": None if id_entidad_padre is None else str(id_entidad_padre),
        "id_usuario": str(usuario.get("id_usuario") or "") or ctx.id_usuario,
        "email": usuario.get("email") or ctx.email,
        "rol": usuario.get("rol") or ctx.rol,
        "origen": ctx.origen if ctx.origen in contexto.ORIGENES else "API",
        "ip": ctx.ip,
        "user_agent": ctx.user_agent,
        "id_solicitud": ctx.id_solicitud,
        "resumen": resumen,
        "cambios": _json(cambios),
        "datos": _json(datos),
    })


def registrar_evento_aparte(accion: str, resumen: str, **kwargs: Any) -> None:
    """
    Registra el evento en una transacción propia y la confirma. Nunca lanza excepciones.
    Usar solo cuando la operación principal no se guarda (denegada, rechazada o con error)
    y SIN eventos pendientes en la sesión principal (esperaría su bloqueo de la cadena).
    """
    from app.core.database import SessionLocalPG  # evita importación circular

    try:
        with SessionLocalPG() as db:
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            registrar_evento(db, accion, resumen, **kwargs)
            db.commit()
    except Exception:  # noqa: BLE001 - la auditoría aparte nunca debe tumbar la respuesta
        logger.exception("No se pudo registrar el evento de auditoría %s", accion)
