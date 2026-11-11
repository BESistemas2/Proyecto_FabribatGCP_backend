# app/modulos/auditoria/middleware.py
"""
Middleware de contexto: a cada solicitud le asigna un id (o respeta X-Request-ID), toma la
IP del cliente y el navegador, y deja todo en el contexto para la auditoría y los logs.
Al terminar escribe una línea de log técnico con estado y duración.

Es ASGI puro (no BaseHTTPMiddleware) para que el contexto también cubra las tareas en
segundo plano de la solicitud (p. ej. el Auto-Match tras cargar un extracto).

IP: uvicorn ya la resuelve desde X-Forwarded-For solo cuando la solicitud llega desde un
proxy de confianza (Traefik), según FORWARDED_ALLOW_IPS en el Dockerfile.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import time
import uuid

from app.core import contexto

logger = logging.getLogger("fabribat.solicitudes")

_ID_VALIDO = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
RUTAS_SIN_LOG = ("/health",)


def _ip_valida(valor):
    try:
        return str(ipaddress.ip_address(valor)) if valor else None
    except ValueError:  # p. ej. un socket unix o el cliente de pruebas
        return None


def _origen(cabeceras: dict) -> str:
    """WEB si viene del navegador (la aplicación web envía Origin o Referer), API si no."""
    return "WEB" if cabeceras.get("origin") or cabeceras.get("referer") else "API"


class MiddlewareContexto:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        cabeceras = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        pedido = cabeceras.get("x-request-id", "")
        id_solicitud = pedido if _ID_VALIDO.match(pedido) else uuid.uuid4().hex
        cliente = scope.get("client") or (None, None)
        ctx = contexto.Contexto(
            origen=_origen(cabeceras),
            id_solicitud=id_solicitud,
            ip=_ip_valida(cliente[0]),
            user_agent=(cabeceras.get("user-agent") or "")[:300] or None,
            metodo=scope.get("method"),
            ruta=scope.get("path"),
        )
        token = contexto.iniciar(ctx)
        estado = {"codigo": 500}
        inicio = time.perf_counter()

        async def enviar(mensaje):
            if mensaje["type"] == "http.response.start":
                estado["codigo"] = mensaje["status"]
                mensaje.setdefault("headers", [])
                mensaje["headers"] = list(mensaje["headers"]) + [(b"x-request-id", id_solicitud.encode())]
            await send(mensaje)

        try:
            await self.app(scope, receive, enviar)
        finally:
            if ctx.ruta not in RUTAS_SIN_LOG:
                duracion = round((time.perf_counter() - inicio) * 1000)
                nivel = logging.WARNING if estado["codigo"] >= 500 else logging.INFO
                logger.log(nivel, "%s %s %s %sms", ctx.metodo, ctx.ruta, estado["codigo"], duracion,
                           extra={"metodo": ctx.metodo, "ruta": ctx.ruta, "estado": estado["codigo"],
                                  "duracion_ms": duracion, "ip": ctx.ip})
            contexto.restaurar(token)
