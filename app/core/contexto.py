# app/core/contexto.py
"""
Contexto de la solicitud en curso: quién, desde dónde y con qué identificador.

Lo llena el middleware de auditoría al entrar una solicitud y lo completa la dependencia
de autenticación con el usuario. Lo leen:
- la auditoría (registrar_evento) para saber quién hizo la acción;
- la base de datos (cada transacción recibe app.origen, app.usuario y app.id_solicitud);
- los logs técnicos (id_solicitud en cada línea).

FastAPI ejecuta dependencias y endpoints síncronos en hilos con una copia del contexto.
Por eso el contexto es un objeto mutable: la dependencia de autenticación modifica el mismo
objeto y el endpoint lo ve, aunque corran en hilos distintos.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterator, Optional

ORIGENES = ("WEB", "API", "SCHEDULER", "SQL_DIRECTO", "MIGRADO")


@dataclass
class Contexto:
    origen: str = "API"
    id_solicitud: Optional[str] = None
    ip: Optional[str] = None
    user_agent: Optional[str] = None
    metodo: Optional[str] = None
    ruta: Optional[str] = None
    id_usuario: Optional[str] = None
    email: Optional[str] = None
    rol: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


_contexto: ContextVar[Optional[Contexto]] = ContextVar("fabribat_contexto", default=None)

# Valores del proceso cuando no hay solicitud (el scheduler los cambia al arrancar)
_por_defecto = Contexto()


def fijar_por_defecto(origen: str, email: Optional[str] = None) -> None:
    """Origen y usuario del proceso fuera de una solicitud (p. ej. SCHEDULER)."""
    global _por_defecto
    _por_defecto = Contexto(origen=origen, email=email)


def actual() -> Contexto:
    """Contexto vigente: el de la solicitud o, si no hay, el del proceso."""
    return _contexto.get() or _por_defecto


def iniciar(ctx: Contexto):
    """Fija el contexto; devuelve el token para restaurar el anterior."""
    return _contexto.set(ctx)


def restaurar(token) -> None:
    _contexto.reset(token)


@contextmanager
def con_contexto(**valores: Any) -> Iterator[Contexto]:
    """Contexto temporal, p. ej. para una tarea del scheduler o un script."""
    ctx = replace(_por_defecto, **valores)
    token = _contexto.set(ctx)
    try:
        yield ctx
    finally:
        _contexto.reset(token)


def fijar_usuario(usuario: Dict[str, Any]) -> None:
    """La dependencia de autenticación registra al usuario en el contexto de la solicitud."""
    ctx = _contexto.get()
    if ctx is None:
        return
    ctx.id_usuario = str(usuario.get("id_usuario") or "") or None
    ctx.email = usuario.get("email") or None
    ctx.rol = usuario.get("rol") or None


def ajustes_pg(ctx: Optional[Contexto] = None) -> Dict[str, str]:
    """Valores para set_config(...) al iniciar cada transacción."""
    ctx = ctx or actual()
    return {
        "origen": ctx.origen or "API",
        "usuario": ctx.email or "",
        "id_solicitud": ctx.id_solicitud or "",
    }
