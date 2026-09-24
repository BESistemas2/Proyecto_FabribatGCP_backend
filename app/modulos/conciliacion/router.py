from datetime import date
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db_pg
from app.modulos.conciliacion.schemas import (
    ActaResponse, 
    EjecutarConciliacionRequest
)
from app.modulos.conciliacion.service import ConciliacionService
from app.modulos.conciliacion.repository import ConciliacionRepository


router = APIRouter(prefix="/api/v1/conciliacion", tags=["Conciliación"])


@router.get("/mayor-editado", summary="Consultar Libro Mayor de Dynamics 365 BC")
def obtener_mayor_editado(
    fecha_inicio: date = Query(..., description="Fecha inicial (YYYY-MM-DD)"),
    fecha_fin: date = Query(..., description="Fecha final (YYYY-MM-DD)"),
    cuenta_contable: str = Query(..., description="Cuenta contable o Grupo de Registro de BC"),
    db: Session = Depends(get_db_pg)
):
    service = ConciliacionService(db)
    df_mayor, error = service.obtener_y_preparar_mayor(
        fecha_inicio=fecha_inicio.strftime("%Y-%m-%d"),
        fecha_fin=fecha_fin.strftime("%Y-%m-%d"),
        cuenta_contable=cuenta_contable
    )
    
    if error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, 
            detail={"error_origen": "Microsoft Dynamics 365 BC", "detalle": str(error)}
        )

    return {
        "status": "success",
        "total_registros": len(df_mayor) if df_mayor is not None else 0,
        "data": df_mayor.to_dict(orient="records") if df_mayor is not None and not df_mayor.empty else []
    }


@router.post(
    "/ejecutar", 
    status_code=status.HTTP_201_CREATED,
    summary="Ejecutar motor de coincidencia (Match) del periodo"
)
def ejecutar_conciliacion(
    payload: EjecutarConciliacionRequest,
    fecha_inicio: date = Query(..., description="Fecha inicial del periodo"),
    fecha_fin: date = Query(..., description="Fecha final del periodo"),
    db: Session = Depends(get_db_pg)
):
    service = ConciliacionService(db)
    return service.ejecutar_conciliacion_periodo(
        id_cuenta=payload.id_cuenta,
        periodo=payload.periodo,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
        creado_por=payload.creado_por
    )


@router.get(
    "/actas", 
    response_model=List[ActaResponse],
    summary="Listar historial de actas de conciliación"
)
def listar_actas(limit: int = 50, db: Session = Depends(get_db_pg)):
    repo = ConciliacionRepository(db)
    return repo.listar_actas(limit=limit)


@router.get(
    "/actas/{id_acta}", 
    response_model=ActaResponse,
    summary="Obtener detalle de un acta y sus partidas en tránsito"
)
def obtener_acta_detalle(id_acta: UUID, db: Session = Depends(get_db_pg)):
    repo = ConciliacionRepository(db)
    acta = repo.obtener_acta_por_id(id_acta)
    if not acta:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="El acta especificada no existe."
        )
    return acta