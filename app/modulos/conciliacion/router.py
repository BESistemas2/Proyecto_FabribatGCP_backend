import logging
from datetime import date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.config import EMAIL_DOMINIOS_PERMITIDOS
from app.core.database import get_db_pg
from app.modulos.auditoria.servicio import registrar_evento
from app.modulos.conciliacion.correo_acta import construir_correo_acta, nombre_archivo
from app.modulos.conciliacion.repository import ConciliacionRepository
from app.modulos.conciliacion import corte_inicial
from app.modulos.conciliacion.dashboard import DashboardService
from app.modulos.conciliacion.schemas import (
    ActaDetalleResponse,
    CorteInicialIn,
    CorteInicialOut,
    ActaResponse,
    EjecutarConciliacionPayload,
    EnviarReporteEmailPayload,
    PatronGastoIn,
    PatronGastoOut,
)
from app.modulos.conciliacion.gastos_bancarios import GastosBancariosService
from app.modulos.conciliacion.services.reporte_gastos_excel import (
    generar_excel_gastos_bancarios, nombre_archivo_gastos,
)
from app.modulos.conciliacion.service import ConciliacionService
from app.modulos.conciliacion.services.reporte_cruces_excel import generar_excel_detalle_cruces, nombre_archivo_cruces
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


def _auditar_descarga(db: Session, accion: str, archivo: str, entidad: str, id_entidad,
                      desde: Optional[date] = None, hasta: Optional[date] = None) -> None:
    """Las descargas se registran (sin el contenido del archivo)."""
    registrar_evento(db, accion, f"Descargó {archivo}", entidad=entidad, id_entidad=id_entidad,
                     datos={"archivo": archivo, "desde": desde, "hasta": hasta})
    db.commit()


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


# --- DASHBOARD -----------------------------------------------------------------

@router.get("/dashboard", summary="Indicadores por cuenta y consolidados de un rango de meses")
def obtener_dashboard(
    desde: str = Query(..., description="AAAA-MM"),
    hasta: str = Query(..., description="AAAA-MM"),
    banco: Optional[str] = Query(None, description="Nombre del banco (vacío = todos)"),
    id_cuenta: Optional[UUID] = Query(None),
    db: Session = Depends(get_db_pg),
):
    return DashboardService(db).resumen(desde, hasta, banco, id_cuenta)


# --- CORTE INICIAL POR CUENTA ------------------------------------------------

@router.get("/cortes/{id_cuenta}", response_model=Optional[CorteInicialOut],
            summary="Corte inicial de la cuenta (null si aún no tiene)")
def obtener_corte_inicial(id_cuenta: UUID, db: Session = Depends(get_db_pg)):
    return corte_inicial.obtener(db, id_cuenta)


@router.put("/cortes/{id_cuenta}", response_model=CorteInicialOut,
            summary="Crear o actualizar el corte inicial (debe cuadrar)")
def guardar_corte_inicial(id_cuenta: UUID, payload: CorteInicialIn, db: Session = Depends(get_db_pg),
                          usuario: dict = Depends(requerir_operador)):
    return corte_inicial.guardar(db, id_cuenta, payload, usuario["email"])


# --- GASTOS BANCARIOS --------------------------------------------------------

@router.get("/gastos-bancarios", summary="Gastos bancarios por mes (detalle, total y asiento si ya cruzó)")
def obtener_gastos_bancarios(id_cuenta: UUID, fecha_inicio: date, fecha_fin: date,
                             db: Session = Depends(get_db_pg)):
    return GastosBancariosService(db).resumen(id_cuenta, fecha_inicio, fecha_fin)


@router.get("/gastos-bancarios/excel", summary="Excel de gastos bancarios por mes (insumo para el asiento)")
def exportar_gastos_bancarios(id_cuenta: UUID, fecha_inicio: date, fecha_fin: date,
                              db: Session = Depends(get_db_pg)):
    datos = GastosBancariosService(db).resumen(id_cuenta, fecha_inicio, fecha_fin)
    _auditar_descarga(db, "conciliacion.gastos_bancarios.descargar", nombre_archivo_gastos(datos),
                      "cuenta_bancaria", id_cuenta, fecha_inicio, fecha_fin)
    return StreamingResponse(
        generar_excel_gastos_bancarios(datos),
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo_gastos(datos)}"'},
    )


@router.get("/patrones-gastos", response_model=List[PatronGastoOut],
            summary="Catálogo de patrones de gastos bancarios")
def listar_patrones_gastos(db: Session = Depends(get_db_pg)):
    return GastosBancariosService(db).listar_patrones()


@router.post("/patrones-gastos", response_model=PatronGastoOut, status_code=status.HTTP_201_CREATED,
             summary="Agregar un patrón de gasto bancario (ADMIN)")
def crear_patron_gasto(payload: PatronGastoIn, db: Session = Depends(get_db_pg),
                       admin: dict = Depends(requerir_rol_admin)):
    return GastosBancariosService(db).guardar_patron(payload.model_dump(), admin["email"])


@router.put("/patrones-gastos/{id_patron}", response_model=PatronGastoOut,
            summary="Modificar un patrón de gasto bancario (ADMIN)")
def actualizar_patron_gasto(id_patron: int, payload: PatronGastoIn, db: Session = Depends(get_db_pg),
                            admin: dict = Depends(requerir_rol_admin)):
    return GastosBancariosService(db).guardar_patron(payload.model_dump(), admin["email"], id_patron)


@router.delete("/patrones-gastos/{id_patron}", status_code=status.HTTP_204_NO_CONTENT,
               summary="Eliminar un patrón de gasto bancario (ADMIN)")
def eliminar_patron_gasto(id_patron: int, db: Session = Depends(get_db_pg),
                          admin: dict = Depends(requerir_rol_admin)):
    GastosBancariosService(db).eliminar_patron(id_patron)


# --- REPORTES ----------------------------------------------------------------

@router.get("/actas/{id_acta}/exportar-excel", summary="Descargar el informe Excel del acta")
@router.get("/actas/{id_acta}/export", include_in_schema=False)
def exportar_acta_excel(id_acta: UUID, db: Session = Depends(get_db_pg),
                        usuario: dict = Depends(obtener_usuario_autenticado)):
    datos = ConciliacionService(db).obtener_datos_reporte_acta(id_acta, usuario.get("email", ""))
    _auditar_descarga(db, "conciliacion.acta.descargar", nombre_archivo(datos), "acta", id_acta)
    return StreamingResponse(
        generar_excel_informe_conciliacion(datos),
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo(datos)}"'},
    )


@router.get("/reporte-cruces", summary="Excel con el detalle de cruces, reversos y pendientes (validación de reglas)")
def exportar_detalle_cruces(id_cuenta: UUID, fecha_inicio: date, fecha_fin: date,
                            db: Session = Depends(get_db_pg),
                            usuario: dict = Depends(obtener_usuario_autenticado)):
    datos = ConciliacionService(db).obtener_datos_reporte_cruces(id_cuenta, fecha_inicio, fecha_fin,
                                                                 usuario.get("email", ""))
    _auditar_descarga(db, "conciliacion.reporte_cruces.descargar", nombre_archivo_cruces(datos),
                      "cuenta_bancaria", id_cuenta, fecha_inicio, fecha_fin)
    return StreamingResponse(
        generar_excel_detalle_cruces(datos),
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{nombre_archivo_cruces(datos)}"'},
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
    registrar_evento(db, "conciliacion.acta.enviar_correo",
                     f"Envió el acta {datos.get('periodo_nombre', '')} a {len(destinatarios)} destinatario(s)",
                     entidad="acta", id_entidad=id_acta,
                     datos={"destinatarios": destinatarios, "con_comentario": bool(payload.comentario)})
    db.commit()
    background_tasks.add_task(enviar_correo_con_adjunto, destinatarios=destinatarios, **correo)
    logger.info("%s envió el acta %s a %s", usuario.get("email"), id_acta, destinatarios)
    return {"status": "processing",
            "message": f"El informe se está enviando a {len(destinatarios)} destinatario(s)."}
