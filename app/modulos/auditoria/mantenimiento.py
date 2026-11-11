# app/modulos/auditoria/mantenimiento.py
"""
Tareas de mantenimiento:

- revisar_migraciones(): al arrancar el backend compara los archivos de migrations/ con
  public.schema_migrations y avisa (log + evento CRITICO) si falta alguna.
- mantenimiento_diario(): lo corre el scheduler una vez al día: crea las particiones de los
  próximos meses, verifica la cadena de hash y borra los meses que superan la retención.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List

from sqlalchemy import text

from app.core import config
from app.core.database import SessionLocalPG
from app.modulos.auditoria.servicio import auditoria_disponible, registrar_evento, registrar_evento_aparte

logger = logging.getLogger(__name__)

CARPETA_MIGRACIONES = Path(__file__).resolve().parents[3] / "migrations"
_ARCHIVO = re.compile(r"^(\d{3})_.+\.sql$")
MESES_ADELANTE = 2


def migraciones_en_disco(carpeta: Path = CARPETA_MIGRACIONES) -> Dict[int, str]:
    if not carpeta.is_dir():
        return {}
    return {int(m.group(1)): p.name for p in sorted(carpeta.iterdir()) if (m := _ARCHIVO.match(p.name))}


def migraciones_faltantes(en_disco: Dict[int, str], aplicadas: set) -> List[str]:
    return [nombre for numero, nombre in sorted(en_disco.items()) if numero not in aplicadas]


def _avisado_hace_poco() -> bool:
    """Con varios workers cada uno revisa al arrancar: un solo evento cada 10 minutos."""
    try:
        with SessionLocalPG() as db:
            if not auditoria_disponible(db):
                return False
            return bool(db.execute(text(
                "SELECT EXISTS (SELECT 1 FROM auditoria.eventos WHERE accion = 'sistema.migracion.faltar' "
                "AND ocurrido_en > now() - interval '10 minutes')")).scalar())
    except Exception:  # noqa: BLE001
        return False


def revisar_migraciones() -> List[str]:
    """Devuelve los archivos que faltan por ejecutar. Nunca lanza excepciones."""
    en_disco = migraciones_en_disco()
    if not en_disco:
        logger.info("No se encontró la carpeta migrations/; se omite la revisión de migraciones")
        return []
    try:
        with SessionLocalPG() as db:
            if db.execute(text("SELECT to_regclass('public.schema_migrations')")).scalar() is None:
                aplicadas = set()
            else:
                aplicadas = set(db.execute(text("SELECT numero FROM public.schema_migrations")).scalars())
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo revisar las migraciones")
        return []
    faltan = migraciones_faltantes(en_disco, aplicadas)
    if faltan:
        logger.error("FALTAN MIGRACIONES por ejecutar en la base: %s", ", ".join(faltan))
        if _avisado_hace_poco():
            return faltan
        registrar_evento_aparte("sistema.migracion.faltar",
                                f"Al arrancar faltan migraciones: {', '.join(faltan)}",
                                datos={"faltantes": faltan})
    else:
        logger.info("Migraciones al día (última: %s)", en_disco[max(en_disco)])
    return faltan


def mantenimiento_diario() -> Dict[str, object]:
    """Particiones, verificación de la cadena y retención. Nunca lanza excepciones."""
    resultado: Dict[str, object] = {}
    try:
        with SessionLocalPG() as db:
            if not auditoria_disponible(db):
                return {"omitido": "auditoría no instalada"}
            resultado["particiones_creadas"] = db.execute(text(
                "SELECT auditoria.crear_particiones(current_date, "
                "(date_trunc('month', current_date) + make_interval(months => :m))::date)"),
                {"m": MESES_ADELANTE}).scalar()
            db.commit()

            fila = db.execute(text("SELECT * FROM auditoria.verificar_cadena()")).mappings().one()
            resultado["cadena"] = dict(fila)
            if fila["correcta"]:
                logger.info("Cadena de auditoría íntegra (%s eventos)", fila["eventos_revisados"])
            else:
                logger.error("CADENA DE AUDITORÍA ROTA en el evento %s: %s",
                             fila["id_evento_con_error"], fila["detalle"])
                registrar_evento(db, "auditoria.cadena.alertar",
                                 f"Cadena rota en el evento {fila['id_evento_con_error']}: {fila['detalle']}",
                                 resultado="ERROR", datos=dict(fila))
            db.commit()

            resultado["meses_purgados"] = db.execute(
                text("SELECT auditoria.purgar_particiones(:anios)"),
                {"anios": config.AUDITORIA_RETENCION_ANIOS}).scalar()
            db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Falló el mantenimiento de auditoría")
        resultado["error"] = True
    return resultado
