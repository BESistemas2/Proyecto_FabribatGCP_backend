# app/modulos/conciliacion/auto_match.py
"""
Auto-Match automático por cuenta.

Se dispara de dos formas:
  1. Programado (app/scheduler.py), según la configuración de cada cuenta:
       DIARIA   -> todos los días desde `auto_match_hora`, repitiendo cada
                   `auto_match_intervalo_minutos` (1440 = una vez al día).
       SEMANAL  -> los lunes a `auto_match_hora`.
       MENSUAL  -> el día 1 de cada mes a `auto_match_hora`.
     Si el servidor estuvo apagado a la hora indicada, se ejecuta en cuanto vuelve (mismo día).
  2. Tras cada carga de extracto exitosa, para los meses que cubre el extracto.

Concilia el mes en curso y, durante los primeros DIAS_MES_ANTERIOR días, también el mes
anterior (si su acta no está cerrada). Los meses sin movimientos del banco se omiten.
Un candado de PostgreSQL por cuenta evita ejecuciones simultáneas (varios workers,
scheduler + carga manual).
"""
import logging
from datetime import date, datetime, time, timedelta
from typing import Iterable, List, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select, text

from app.core.database import SessionLocalPG, engine_pg
from app.modulos.bancos.models import CuentaBancaria, Movimiento
from app.modulos.conciliacion.service import ConciliacionService, rango_de_periodo

logger = logging.getLogger(__name__)

TZ_EC = ZoneInfo("America/Guayaquil")
HORA_POR_DEFECTO = time(4, 0)
DIAS_MES_ANTERIOR = 10
INTERVALO_MIN, INTERVALO_MAX = 15, 1440
USUARIO_SISTEMA = "sistema (auto-match)"


def ahora_ec() -> datetime:
    return datetime.now(TZ_EC).replace(tzinfo=None)


def periodo_de(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def periodos_programados(hoy: date) -> List[str]:
    periodos = [periodo_de(hoy)]
    if hoy.day <= DIAS_MES_ANTERIOR:
        periodos.append(periodo_de(hoy.replace(day=1) - timedelta(days=1)))
    return periodos


def periodos_entre(desde: Optional[date], hasta: Optional[date]) -> List[str]:
    """Meses que cubre un extracto (máximo 3, del más reciente al más antiguo)."""
    if not desde or not hasta:
        return []
    out, d = [], date(hasta.year, hasta.month, 1)
    while d >= date(desde.year, desde.month, 1) and len(out) < 3:
        out.append(periodo_de(d))
        d = (d - timedelta(days=1)).replace(day=1)
    return out


def toca_ejecutar(cuenta: CuentaBancaria, ahora: datetime) -> bool:
    """¿Debe correr el Auto-Match programado de esta cuenta en este momento?"""
    if not (cuenta.activo and cuenta.auto_match_habilitado):
        return False
    inicio_hoy = datetime.combine(ahora.date(), cuenta.auto_match_hora or HORA_POR_DEFECTO)
    if ahora < inicio_hoy:
        return False
    ultima = cuenta.auto_match_ultima_ejecucion
    frecuencia = (cuenta.auto_match_frecuencia or "DIARIA").upper()

    if frecuencia == "SEMANAL" and ahora.weekday() != 0:
        return False
    if frecuencia == "MENSUAL" and ahora.day != 1:
        return False
    if ultima is None or ultima < inicio_hoy:
        return True
    if frecuencia == "DIARIA":
        intervalo = min(max(cuenta.auto_match_intervalo_minutos or INTERVALO_MAX, INTERVALO_MIN), INTERVALO_MAX)
        return intervalo < INTERVALO_MAX and ahora - ultima >= timedelta(minutes=intervalo)
    return False


def _tiene_movimientos(db, id_cuenta: UUID, periodo: str) -> bool:
    f_ini, f_fin = rango_de_periodo(periodo)
    n = db.execute(
        select(func.count()).select_from(Movimiento).where(
            Movimiento.id_cuenta == id_cuenta,
            Movimiento.fecha_transaccion.between(f_ini, f_fin),
        )
    ).scalar_one()
    return n > 0


def ejecutar_auto_match_cuenta(id_cuenta: UUID, origen: str, periodos: Optional[Iterable[str]] = None) -> str:
    """
    Concilia los periodos indicados (o los programados) de una cuenta y guarda el resultado
    en la cuenta. Nunca lanza excepciones: devuelve el resumen (también va al log).
    """
    clave = f"automatch:{id_cuenta}"
    with engine_pg.connect() as candado:
        if not candado.execute(text("SELECT pg_try_advisory_lock(hashtext(:k))"), {"k": clave}).scalar():
            logger.info("Auto-Match %s (%s): ya hay una ejecución en curso, se omite", id_cuenta, origen)
            return "Omitido: ya hay una ejecución en curso."
        candado.commit()  # el candado es de sesión: sigue tomado tras el commit
        try:
            return _ejecutar(id_cuenta, origen, periodos)
        finally:
            candado.execute(text("SELECT pg_advisory_unlock(hashtext(:k))"), {"k": clave})
            candado.commit()


def _ejecutar(id_cuenta: UUID, origen: str, periodos: Optional[Iterable[str]]) -> str:
    db = SessionLocalPG()
    try:
        cuenta = db.get(CuentaBancaria, id_cuenta)
        if not cuenta:
            return "Cuenta no encontrada."
        ahora = ahora_ec()
        lista = list(periodos) if periodos else periodos_programados(ahora.date())
        partes, hubo_error = [], False
        servicio = ConciliacionService(db)
        for periodo in lista:
            if not _tiene_movimientos(db, cuenta.id_cuenta, periodo):
                partes.append(f"{periodo}: sin extracto")
                continue
            f_ini, f_fin = rango_de_periodo(periodo)
            try:
                r = servicio.ejecutar_conciliacion_periodo(cuenta.id_cuenta, periodo, f_ini, f_fin, USUARIO_SISTEMA)
                partes.append(f"{periodo}: {r['movimientos_conciliados']} cruces, "
                              + (f"{r['reversos_bc']} reversos BC, " if r.get('reversos_bc') else "")
                              + f"{r['total_partidas_en_transito']} pendientes")
            except HTTPException as e:
                if e.status_code == 409 and "CERRADO" in str(e.detail).upper():
                    partes.append(f"{periodo}: acta cerrada")
                else:
                    hubo_error = True
                    partes.append(f"{periodo}: error ({e.detail})")
            except Exception as e:  # noqa: BLE001 - se registra y se sigue con el siguiente periodo
                db.rollback()
                hubo_error = True
                logger.exception("Auto-Match %s %s falló", cuenta.codigo_banco_bc, periodo)
                partes.append(f"{periodo}: error interno ({type(e).__name__})")

        resumen = ("ERROR · " if hubo_error else "OK · ") + f"{origen} · " + "; ".join(partes)
        cuenta = db.get(CuentaBancaria, id_cuenta)  # la sesión pudo hacer rollback
        cuenta.auto_match_ultima_ejecucion = ahora
        cuenta.auto_match_ultimo_resultado = resumen[:500]
        db.commit()
        logger.info("Auto-Match %s: %s", cuenta.codigo_banco_bc, resumen)
        return resumen
    except Exception:
        db.rollback()
        logger.exception("Auto-Match de la cuenta %s falló", id_cuenta)
        return "Error interno."
    finally:
        db.close()
