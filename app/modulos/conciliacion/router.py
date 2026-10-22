import logging
from datetime import date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import EMAIL_DOMINIOS_PERMITIDOS
from app.core.database import get_db_pg
from app.modulos.conciliacion.correo_acta import construir_correo_acta, nombre_archivo
from app.modulos.conciliacion.repository import ConciliacionRepository
from app.modulos.conciliacion.schemas import (
    ActaDetalleResponse,
    ActaResponse,
    EjecutarConciliacionPayload,
    EnviarReporteEmailPayload,
)
from app.modulos.conciliacion.service import ConciliacionService
from app.modulos.conciliacion.services.reporte_excel import generar_excel_informe_conciliacion
from app.modulos.identidad.dependencies import (
    obtener_usuario_autenticado,
    requerir_operador,
    requerir_rol_admin,
)
from app.utils.email_service import enviar_correo_con_adjunto

logger = logging.getLogger(__name__)

# Lectura: cualquier usuario con sesión. Escritura: ADMIN o CONTABILIDAD (ver cada ruta).
router = APIRouter(
    prefix="/api/v1/conciliacion",
    tags=["Conciliación Bancaria"],
    dependencies=[Depends(obtener_usuario_autenticado)],
)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# --- WORKBENCH ---------------------------------------------------------------

@router.get("/mayor-editado", summary="Libro de bancos de Business Central (pendientes del periodo)")
def obtener_mayor_editado(
    fecha_inicio: date,
    fecha_fin: date,
    cuenta_contable: Optional[str] = Query(None, description="Cuenta contable / código / grupo de BC"),
    id_cuenta: Optional[UUID] = Query(None, description="Preferido: ID de la cuenta bancaria"),
    db: Session = Depends(get_db_pg),
):
    if not id_cuenta and not cuenta_contable:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Indique id_cuenta o cuenta_contable.")
    return ConciliacionService(db).obtener_y_preparar_mayor(fecha_inicio, fecha_fin, cuenta_contable, id_cuenta)


@router.post("/ejecutar", summary="Ejecutar el cruce automático del periodo")
def ejecutar_conciliacion(
    payload: EjecutarConciliacionPayload,
    db: Session = Depends(get_db_pg),
    usuario: dict = Depends(requerir_operador),
):
    return ConciliacionService(db).ejecutar_conciliacion_periodo(
        id_cuenta=payload.id_cuenta,
        periodo=payload.periodo,
        f_ini=payload.fecha_inicio,
        f_fin=payload.fecha_fin,
        creado_por=usuario["email"],
    )


@router.get("/grupos", summary="Movimientos conciliados agrupados (banco -> asientos BC)")
def get_grupos_conciliados(id_cuenta: UUID, fecha_inicio: date, fecha_fin: date, db: Session = Depends(get_db_pg)):
    return ConciliacionRepository(db).obtener_grupos_conciliados(id_cuenta, fecha_inicio, fecha_fin)


# --- ACTAS -------------------------------------------------------------------

@router.get("/actas", response_model=List[ActaResponse], summary="Historial de actas")
def listar_actas(limit: int = Query(50, ge=1, le=200), id_cuenta: Optional[UUID] = None,
                 db: Session = Depends(get_db_pg)):
    return ConciliacionService(db).listar_actas(limit, id_cuenta)


@router.get("/actas/{id_acta}", response_model=ActaDetalleResponse, summary="Acta con sus partidas en tránsito")
def obtener_acta_detalle(id_acta: UUID, db: Session = Depends(get_db_pg)):
    return ConciliacionService(db).obtener_detalle_acta(id_acta)


@router.post("/actas/{id_acta}/cerrar", response_model=ActaResponse,
             summary="Cerrar el acta (congela las partidas en tránsito)")
def cerrar_acta(id_acta: UUID, db: Session = Depends(get_db_pg), usuario: dict = Depends(requerir_operador)):
    return ConciliacionService(db).cerrar_acta(id_acta, usuario["email"])


@router.post("/actas/{id_acta}/reabrir", response_model=ActaResponse, summary="Reabrir un acta cerrada (ADMIN)")
def reabrir_acta(id_acta: UUID, db: Session = Depends(get_db_pg), usuario: dict = Depends(requerir_rol_admin)):
    return ConciliacionService(db).reabrir_acta(id_acta, usuario["email"])


# --- REPORTES ----------------------------------------------------------------

@router.get("/actas/{id_acta}/exportar-excel", summary="Descargar el informe Excel del acta")
@router.get("/actas/{id_acta}/export", include_in_schema=False)
def exportar_acta_excel(id_acta: UUID, db: Session = Depends(get_db_pg),
                        usuario: dict = Depends(obtener_usuario_autenticado)):
    datos = ConciliacionService(db).obtener_datos_reporte_acta(id_acta, usuario.get("email", ""))
    return StreamingResponse(
        generar_excel_informe_conciliacion(datos),
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo(datos)}"'},
    )


@router.post("/actas/{id_acta}/enviar-correo", status_code=status.HTTP_202_ACCEPTED,
             summary="Enviar el informe del acta por correo")
def enviar_acta_email(
    id_acta: UUID,
    payload: EnviarReporteEmailPayload,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db_pg),
    usuario: dict = Depends(requerir_operador),
):
    destinatarios = sorted({str(d).lower() for d in payload.destinatarios})
    no_permitidos = [d for d in destinatarios if d.split("@")[-1] not in EMAIL_DOMINIOS_PERMITIDOS]
    if no_permitidos:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Solo se puede enviar a correos de: {', '.join(EMAIL_DOMINIOS_PERMITIDOS)}. "
            f"No permitidos: {', '.join(no_permitidos)}",
        )

    datos = ConciliacionService(db).obtener_datos_reporte_acta(id_acta, usuario.get("email", ""))
    correo = construir_correo_acta(
        datos, f"Adjunto el informe de conciliación del período {datos['periodo_nombre']}, "
               f"enviado por {usuario.get('email')}.", payload.comentario)
    background_tasks.add_task(enviar_correo_con_adjunto, destinatarios=destinatarios, **correo)
    logger.info("%s envió el acta %s a %s", usuario.get("email"), id_acta, destinatarios)
    return {"status": "processing",
            "message": f"El informe se está enviando a {len(destinatarios)} destinatario(s)."}
