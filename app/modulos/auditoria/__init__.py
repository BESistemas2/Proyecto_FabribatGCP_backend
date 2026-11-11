"""Módulo de auditoría: quién hizo qué, cuándo, desde dónde y qué cambió."""
from app.modulos.auditoria.servicio import (  # noqa: F401
    diferencias,
    registrar_evento,
    registrar_evento_aparte,
)
