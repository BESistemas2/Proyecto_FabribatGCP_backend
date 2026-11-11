# app/modulos/auditoria/router.py
"""Consulta de la auditoría. Solo ADMIN (decisión del plan de auditoría)."""
from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db_pg
from app.modulos.auditoria.repository import AuditoriaRepository
from app.modulos.auditoria.schemas import AccionOut, EventoOut, PaginaEventos, VerificacionOut
from app.modulos.identidad.dependencies import requerir_rol_admin

router = APIRouter(
    prefix="/api/v1/auditoria",
    tags=["Auditoría"],
    dependencies=[Depends(requerir_rol_admin)],
)

MAX_DIAS_CONSULTA = 366


@router.get("/eventos", response_model=PaginaEventos, summary="Buscar eventos de auditoría")
def buscar_eventos(
    desde: Optional[date] = Query(None, description="Por defecto, hace 7 días"),
    hasta: Optional[date] = Query(None, description="Por defecto, hoy"),
    modulo: Optional[str] = None,
    accion: Optional[str] = None,
    email: Optional[str] = Query(None, description="Contiene"),
    entidad: Optional[str] = None,
    id_entidad: Optional[str] = None,
    nivel: Optional[str] = Query(None, pattern="^(INFORMATIVO|IMPORTANTE|CRITICO)$"),
    resultado: Optional[str] = Query(None, pattern="^(OK|ERROR|DENEGADO)$"),
    origen: Optional[str] = Query(None, pattern="^(WEB|API|SCHEDULER|SQL_DIRECTO|MIGRADO)$"),
    texto: Optional[str] = Query(None, description="Contiene, en el resumen"),
    limite: int = Query(100, ge=1, le=500),
    desplazamiento: int = Query(0, ge=0),
    db: Session = Depends(get_db_pg),
):
    hasta = hasta or date.today()
    desde = desde or hasta - timedelta(days=7)
    if desde > hasta:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "La fecha inicial es posterior a la final.")
    if (hasta - desde).days > MAX_DIAS_CONSULTA:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Consulte como máximo un año a la vez.")
    eventos, total = AuditoriaRepository(db).buscar_eventos(
        desde, hasta, modulo=modulo, accion=accion, email=email, entidad=entidad, id_entidad=id_entidad,
        nivel=nivel, resultado=resultado, origen=origen, texto=texto, limite=limite,
        desplazamiento=desplazamiento)
    return {"total": total, "eventos": eventos}


@router.get("/eventos/{id_evento}", response_model=EventoOut, summary="Detalle de un evento")
def obtener_evento(id_evento: int, db: Session = Depends(get_db_pg)):
    evento = AuditoriaRepository(db).obtener_evento(id_evento)
    if not evento:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evento no encontrado.")
    return evento


@router.get("/historial/{entidad}/{id_entidad}", response_model=List[EventoOut],
            summary="Historial de un registro y de sus registros hijos")
def historial_registro(entidad: str, id_entidad: str, db: Session = Depends(get_db_pg)):
    return AuditoriaRepository(db).historial(entidad, id_entidad)


@router.get("/acciones", response_model=List[AccionOut], summary="Catálogo de acciones auditadas")
def listar_acciones(db: Session = Depends(get_db_pg)):
    return AuditoriaRepository(db).listar_acciones()


@router.get("/verificacion", response_model=VerificacionOut, summary="Verificar la cadena de hash ahora")
def verificar_cadena(db: Session = Depends(get_db_pg)):
    return AuditoriaRepository(db).verificar_cadena()
