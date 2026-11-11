# app/scheduler.py
"""
Programador del Auto-Match. Corre como un contenedor aparte (servicio fabribat-scheduler):

    python -m app.scheduler

Cada minuto revisa las cuentas activas con Auto-Match habilitado y ejecuta las que
correspondan según su frecuencia y hora (ver app/modulos/conciliacion/auto_match.py).
Un candado de PostgreSQL garantiza que solo haya un programador activo aunque se
levanten varios contenedores.

Una vez al día (y al arrancar) corre el mantenimiento de auditoría: particiones de los
próximos meses, verificación de la cadena de hash y retención.
"""
import logging
import signal
import time
import uuid

from sqlalchemy import select, text

from app.core import contexto, logs
from app.core.database import SessionLocalPG, engine_pg
from app.modulos.auditoria.mantenimiento import mantenimiento_diario
from app.modulos.bancos.models import CuentaBancaria
from app.modulos.conciliacion.auto_match import USUARIO_SISTEMA, ahora_ec, ejecutar_auto_match_cuenta, toca_ejecutar

logs.configurar("fabribat.scheduler")
logger = logging.getLogger("fabribat.scheduler")
# Todo lo que haga este proceso queda auditado con origen SCHEDULER
contexto.fijar_por_defecto("SCHEDULER", USUARIO_SISTEMA)

INTERVALO_REVISION_SEG = 60
_detener = False


def _parar(*_):
    global _detener
    _detener = True


def cuentas_pendientes() -> list:
    ahora = ahora_ec()
    with SessionLocalPG() as db:
        cuentas = db.execute(
            select(CuentaBancaria).where(CuentaBancaria.activo.is_(True),
                                         CuentaBancaria.auto_match_habilitado.is_(True))
        ).scalars().all()
        return [c.id_cuenta for c in cuentas if toca_ejecutar(c, ahora)]


def main() -> None:
    signal.signal(signal.SIGTERM, _parar)
    signal.signal(signal.SIGINT, _parar)
    with engine_pg.connect() as lider:
        while not _detener and not lider.execute(text("SELECT pg_try_advisory_lock(hashtext('fabribat-scheduler'))")).scalar():
            logger.info("Otro programador está activo; reintento en %ss", INTERVALO_REVISION_SEG)
            time.sleep(INTERVALO_REVISION_SEG)
        lider.commit()
        logger.info("Programador de Auto-Match iniciado (revisión cada %ss)", INTERVALO_REVISION_SEG)
        ultimo_mantenimiento = None
        while not _detener:
            try:
                hoy = ahora_ec().date()
                if ultimo_mantenimiento != hoy:
                    with contexto.con_contexto(id_solicitud=f"mant-{uuid.uuid4().hex[:12]}"):
                        logger.info("Mantenimiento de auditoría: %s", mantenimiento_diario())
                    ultimo_mantenimiento = hoy
                for id_cuenta in cuentas_pendientes():
                    if _detener:
                        break
                    with contexto.con_contexto(id_solicitud=f"auto-{uuid.uuid4().hex[:12]}"):
                        ejecutar_auto_match_cuenta(id_cuenta, "programado")
                lider.execute(text("SELECT 1"))  # mantiene viva la conexión que sostiene el candado
                lider.commit()
            except Exception:  # noqa: BLE001 - el programador nunca debe morir por un error puntual
                logger.exception("Error en la revisión del programador")
            for _ in range(INTERVALO_REVISION_SEG):
                if _detener:
                    break
                time.sleep(1)
    logger.info("Programador detenido")


if __name__ == "__main__":
    main()
