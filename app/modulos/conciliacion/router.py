from datetime import date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

# Importaciones del motor de base de datos y esquemas
from app.core.database import get_db_pg
from app.modulos.conciliacion.schemas import (
    ActaResponse,
    EjecutarConciliacionPayload,
    EnviarReportePayload
)
from app.modulos.conciliacion.service import ConciliacionService
from app.modulos.conciliacion.repository import ConciliacionRepository
from app.modulos.identidad.dependencies import obtener_usuario_autenticado

# Servicios auxiliares (Reportes y Notificaciones)
from app.modulos.conciliacion.services.reporte_excel import (
    generar_excel_informe_conciliacion,
)
from app.utils.email_service import enviar_correo_con_adjunto


# --- SCHEMAS DE ENTRADA / SALIDA COMPLEMENTARIOS ---

class EnviarReporteEmailPayload(BaseModel):
    destinatarios: List[EmailStr] = Field(
        ...,
        description="Lista de correos a los que se enviará el informe de conciliación.",
        examples=[["contabilidad@fabribat.com", "auditoria@fabribat.com"]]
    )
    comentario: Optional[str] = Field(
        None,
        description="Observaciones o notas adicionales para incluir en el cuerpo del correo.",
        examples=["Conciliación mensual aprobada sin observaciones."]
    )


# --- CONFIGURACIÓN DEL ROUTER ---
router = APIRouter(
    prefix="/api/v1/conciliacion", 
    tags=["Conciliación Bancaria"]
)


# --- ENDPOINTS DEL WORKBENCH ---

@router.get("/mayor-editado", summary="Consultar Libro Mayor de Dynamics 365 BC")
def obtener_mayor_editado(
    fecha_inicio: str = Query(..., description="Fecha inicial (YYYY-MM-DD)"),
    fecha_fin: str = Query(..., description="Fecha final (YYYY-MM-DD)"),
    cuenta_contable: str = Query(..., description="Cuenta contable o Grupo de Registro de BC"),
    db: Session = Depends(get_db_pg)
):
    """Obtiene y prepara los movimientos del Libro Mayor de Business Central usando el Service."""
    service = ConciliacionService(db)
    df_mayor, novedades, error = service.obtener_y_preparar_mayor(
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin,
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
        "novedades_sync": novedades,
        "data": df_mayor.to_dict(orient="records") if df_mayor is not None and not df_mayor.empty else []
    }


@router.post(
    "/ejecutar", 
    status_code=status.HTTP_200_OK,
    summary="Ejecutar motor de coincidencia (Match) del período"
)
def ejecutar_conciliacion(
    payload: EjecutarConciliacionPayload,
    db: Session = Depends(get_db_pg),
    usuario_actual: dict = Depends(obtener_usuario_autenticado)
):
    """Ejecuta el cruce automático de conciliación validando token y rol de acceso."""
    service = ConciliacionService(db)
    return service.ejecutar_conciliacion_periodo(
        id_cuenta=payload.id_cuenta,
        periodo=payload.periodo,
        fecha_inicio=payload.fecha_inicio,
        fecha_fin=payload.fecha_fin,
        creado_por=usuario_actual["email"],
        cuenta_contable=payload.cuenta_contable
    )


@router.get("/grupos", summary="Obtener grupos de movimientos conciliados")
def get_grupos_conciliados(
    id_cuenta: str = Query(...),
    fecha_inicio: str = Query(...),
    fecha_fin: str = Query(...),
    db: Session = Depends(get_db_pg)
):
    """Recupera la estructura de árbol/acordeón de los movimientos enlazados."""
    repo = ConciliacionRepository(db)
    return repo.obtener_grupos_conciliados(
        id_cuenta=id_cuenta,
        fecha_inicio=fecha_inicio,
        fecha_fin=fecha_fin
    )


# --- ENDPOINTS DE HISTORIAL Y ACTAS ---

@router.get(
    "/actas", 
    response_model=List[ActaResponse],
    summary="Listar historial de actas de conciliación"
)
def listar_actas(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db_pg)):
    """Obtiene el historial de actas registradas en la base de datos."""
    repo = ConciliacionRepository(db)
    return repo.listar_actas(limit=limit)


@router.get(
    "/actas/{id_acta}", 
    summary="Obtener detalle completo de un acta"
)
def obtener_acta_detalle(id_acta: str, db: Session = Depends(get_db_pg)):
    """Recupera la información del acta y sus partidas en tránsito."""
    repo = ConciliacionRepository(db)
    acta = repo.obtener_acta_por_id(id_acta)
    if not acta:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="El acta especificada no existe en la base de datos."
        )
    return acta


# --- ENDPOINTS DE REPORTES EXCEL Y NOTIFICACIONES EMAIL ---

@router.get("/actas/{id_acta}/exportar-excel", summary="Generar y descargar informe Excel de acta")
@router.get("/actas/{id_acta}/export", include_in_schema=False)
async def exportar_acta_excel(id_acta: str, db: Session = Depends(get_db_pg)):
    """Genera dinámicamente en memoria y descarga el Informe de Conciliación (.xlsx)."""
    service = ConciliacionService(db)
    acta_data = service.obtener_datos_reporte_acta(id_acta)

    excel_stream = generar_excel_informe_conciliacion(acta_data)

    periodo_clean = str(acta_data.get("periodo_nombre", "Periodo")).replace(" ", "_")
    banco_clean = str(acta_data.get("banco", "Banco")).replace(" ", "_").replace("/", "-")
    nombre_archivo = f"Informe_Conciliacion_{banco_clean}_{periodo_clean}.xlsx"

    return StreamingResponse(
        excel_stream,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{nombre_archivo}"'
        }
    )


@router.post("/actas/{id_acta}/enviar-correo", status_code=status.HTTP_202_ACCEPTED, summary="Enviar reporte por email")
async def enviar_acta_email(
    id_acta: str,
    payload: EnviarReporteEmailPayload,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db_pg)
):
    """Genera el reporte Excel en memoria y lo envía por correo electrónico de forma asíncrona."""
    service = ConciliacionService(db)
    acta_data = service.obtener_datos_reporte_acta(id_acta)
    
    excel_stream = generar_excel_informe_conciliacion(acta_data)
    excel_bytes = excel_stream.getvalue()

    periodo_str = acta_data.get("periodo_nombre", "Periodo")
    banco_str = acta_data.get("banco", "Banco")
    nombre_archivo = f"Informe_Conciliacion_{periodo_str.replace(' ', '_')}.xlsx"
    asunto = f"Informe de Conciliación Bancaria - {banco_str} ({periodo_str})"

    cuerpo_html = f"""
    <!DOCTYPE html>
    <html>
      <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.5;">
        <div style="max-width: 600px; margin: 0 auto; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px;">
          <h2 style="color: #1F4E79; margin-top: 0;">Informe de Conciliación Bancaria</h2>
          <p>Estimado equipo,</p>
          <p>Adjunto a este correo encontrarán el informe oficial de conciliación correspondiente al período <b>{periodo_str}</b>.</p>
          
          <table style="border-collapse: collapse; width: 100%; font-size: 13px; margin: 20px 0;">
            <tr style="background-color: #f8f9fa;">
              <td style="padding: 10px; border-bottom: 1px solid #ddd;"><b>Empresa:</b></td>
              <td style="padding: 10px; border-bottom: 1px solid #ddd;">{acta_data.get('empresa', 'Fabribat Cia. Ltda.')}</td>
            </tr>
            <tr>
              <td style="padding: 10px; border-bottom: 1px solid #ddd;"><b>Banco:</b></td>
              <td style="padding: 10px; border-bottom: 1px solid #ddd;">{banco_str}</td>
            </tr>
            <tr style="background-color: #f8f9fa;">
              <td style="padding: 10px; border-bottom: 1px solid #ddd;"><b>Saldo Extracto:</b></td>
              <td style="padding: 10px; border-bottom: 1px solid #ddd; font-family: monospace;">${acta_data.get('saldo_extracto_calculado', 0):,.2f}</td>
            </tr>
            <tr>
              <td style="padding: 10px; border-bottom: 1px solid #ddd;"><b>Saldo Libros:</b></td>
              <td style="padding: 10px; border-bottom: 1px solid #ddd; font-family: monospace;">${acta_data.get('saldo_libros', 0):,.2f}</td>
            </tr>
            <tr style="background-color: #f8f9fa;">
              <td style="padding: 10px; border-bottom: 1px solid #ddd;"><b>Diferencia:</b></td>
              <td style="padding: 10px; border-bottom: 1px solid #ddd; font-family: monospace; color: {'#d9534f' if acta_data.get('diferencia', 0) != 0 else '#5cb85c'}; font-weight: bold;">
                ${acta_data.get('diferencia', 0):,.2f}
              </td>
            </tr>
          </table>

          {f'<div style="background-color: #eef2f7; padding: 12px; border-left: 4px solid #1F4E79; font-size: 13px; margin-bottom: 20px;"><b>Observaciones:</b> {payload.comentario}</div>' if payload.comentario else ''}
          
          <p style="font-size: 11px; color: #777; border-top: 1px solid #eee; padding-top: 10px; margin-top: 30px;">
            Este mensaje fue generado automáticamente por el Workbench de Conciliación Bancaria.
          </p>
        </div>
      </body>
    </html>
    """

    background_tasks.add_task(
        enviar_correo_con_adjunto,
        destinatarios=payload.destinatarios,
        asunto=asunto,
        cuerpo_html=cuerpo_html,
        archivo_bytes=excel_bytes,
        nombre_archivo=nombre_archivo,
    )

    return {
        "status": "processing",
        "message": f"El informe está siendo enviado a {len(payload.destinatarios)} destinatario(s) en segundo plano."
    }


@router.post("/reporte/descargar-excel", summary="Descargar reporte Excel dinámico de conciliación")
def descargar_reporte_excel(
    payload: EnviarReportePayload,
    db: Session = Depends(get_db_pg),
    usuario_actual: dict = Depends(obtener_usuario_autenticado)
):
    """Genera y descarga en vivo el reporte Excel con datos reales de la BD."""
    service = ConciliacionService(db)
    
    excel_stream = service.generar_reporte_excel_real(
        id_cuenta=payload.id_cuenta,
        fecha_inicio=payload.fecha_inicio,
        fecha_fin=payload.fecha_fin,
        usuario_email=usuario_actual["email"]
    )

    filename = f"Informe_Conciliacion_{payload.fecha_inicio}_{payload.fecha_fin}.xlsx"

    return Response(
        content=excel_stream.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )