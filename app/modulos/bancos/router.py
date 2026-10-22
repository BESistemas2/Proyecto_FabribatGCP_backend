import logging
import uuid
from datetime import date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import get_db_pg
from app.modulos.bancos.bc_service import BancosBCService
from app.modulos.bancos.etl import EXTENSIONES_PERMITIDAS
from app.modulos.bancos.models import CargaExtracto, CuentaDestinatario
from app.modulos.bancos.repository import BancosRepository
from app.modulos.bancos.schemas import (
    CargaExtractoResponse,
    CargaUploadResponse,
    CuentaBancariaResponse,
    CuentaBancariaUpdate,
    MovimientoResponse,
)
from app.modulos.bancos.service import BancosService
from app.modulos.conciliacion.auto_match import ejecutar_auto_match_cuenta, periodos_entre
from app.modulos.identidad.dependencies import obtener_usuario_autenticado, requerir_operador, requerir_rol_admin

logger = logging.getLogger(__name__)

TAMANO_MAXIMO_ARCHIVO = 15 * 1024 * 1024  # 15 MB (mismo límite que el frontend)

# Todas las rutas del módulo exigen sesión válida
router = APIRouter(
    prefix="/api/v1/bancos",
    tags=["Bancos"],
    dependencies=[Depends(obtener_usuario_autenticado)],
)

# -------------------------------------------------------------
# 1. CUENTAS BANCARIAS
# -------------------------------------------------------------

@router.post("/cuentas/sincronizar", summary="Sincronizar catálogo de cuentas bancarias desde BC (ADMIN)",
             dependencies=[Depends(requerir_rol_admin)])
def sincronizar_cuentas_bc(db: Session = Depends(get_db_pg)):
    cuentas_bc, error = BancosBCService.sincronizar_catalogo_cuentas_bc()
    if error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=error)

    sincronizados = BancosRepository(db).upsert_cuentas_bancarias_bc(cuentas_bc)
    return {
        "status": "success",
        "total_registros_bc": len(cuentas_bc),
        "total_procesados": sincronizados,
        "data": cuentas_bc,
    }


@router.get("/cuentas", response_model=List[CuentaBancariaResponse], summary="Listar cuentas bancarias activas")
def listar_cuentas_bancarias(db: Session = Depends(get_db_pg)):
    return BancosRepository(db).listar_cuentas_bancarias()


@router.put("/cuentas/{id_cuenta}", response_model=CuentaBancariaResponse,
            summary="Actualizar configuración de una cuenta (institución / RPA / Auto-Match) (ADMIN)",
            dependencies=[Depends(requerir_rol_admin)])
def actualizar_cuenta_bancaria(id_cuenta: UUID, payload: CuentaBancariaUpdate, db: Session = Depends(get_db_pg)):
    cuenta = BancosRepository(db).obtener_cuenta_por_id(id_cuenta)
    if not cuenta:
        raise HTTPException(status_code=404, detail="La cuenta bancaria especificada no existe.")

    cambios = payload.model_dump(exclude_unset=True)
    destinatarios = cambios.pop("auto_match_destinatarios", None)
    for key, value in cambios.items():
        setattr(cuenta, key, value)
    if destinatarios is not None:
        ids = list(dict.fromkeys(destinatarios))
        validos = set(db.execute(text("SELECT id_usuario FROM identidad.usuarios WHERE id_usuario = ANY(:ids)"),
                                 {"ids": ids}).scalars().all())
        faltan = [str(i) for i in ids if i not in validos]
        if faltan:
            raise HTTPException(status_code=400, detail=f"Usuarios inexistentes: {', '.join(faltan)}")
        cuenta.destinatarios = [CuentaDestinatario(id_cuenta=cuenta.id_cuenta, id_usuario=i) for i in ids]
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="La institución bancaria indicada no existe.")
    db.refresh(cuenta)
    return cuenta

# -------------------------------------------------------------
# 2. CARGA DE EXTRACTOS
# -------------------------------------------------------------

@router.post(
    "/cargas/upload",
    response_model=CargaUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Subir y procesar un extracto bancario (ETL)",
)
def cargar_archivo_bancario(
    background: BackgroundTasks,
    id_cuenta: UUID = Form(..., description="ID de la cuenta bancaria"),
    archivo: UploadFile = File(..., description="Extracto en Excel (.xlsx/.xls) o CSV"),
    created_by: Optional[str] = Form(None, description="Ignorado: el usuario se toma del token de sesión"),
    usuario: dict = Depends(requerir_operador),
    db: Session = Depends(get_db_pg),
):
    # Endpoint síncrono a propósito: FastAPI lo ejecuta en un hilo y no bloquea el servidor
    nombre = archivo.filename or ""
    if not nombre.lower().endswith(EXTENSIONES_PERMITIDAS):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                            detail="Formato no soportado. Sube un archivo .xlsx, .xls o .csv.")

    file_bytes = archivo.file.read(TAMANO_MAXIMO_ARCHIVO + 1)
    if len(file_bytes) > TAMANO_MAXIMO_ARCHIVO:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail="El archivo supera el máximo de 15 MB.")
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El archivo está vacío.")

    repo = BancosRepository(db)
    cuenta = repo.obtener_cuenta_por_id(id_cuenta)
    if not cuenta:
        raise HTTPException(status_code=404, detail="La cuenta bancaria especificada no existe.")

    id_carga = uuid.uuid4()
    repo.crear_carga(CargaExtracto(
        id_carga=id_carga,
        id_cuenta=id_cuenta,
        id_institucion=cuenta.id_institucion,
        nombre_archivo=nombre[:255],
        created_by=usuario.get("email") or "desconocido",
        estado="Iniciado",
    ))

    exito, mensaje, resultado = BancosService(db).procesar_archivo_bancos_service(
        file_bytes=file_bytes,
        id_carga=id_carga,
        cuenta=cuenta,
        filename_original=nombre,
    )
    if not exito:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"id_carga": str(id_carga), "error": mensaje},
        )

    # Auto-Match tras la carga (en segundo plano: la respuesta no espera a Business Central)
    periodos = periodos_entre(resultado.fecha_desde, resultado.fecha_hasta)
    auto_match = bool(cuenta.activo and cuenta.auto_match_habilitado and periodos and resultado.movimientos)
    if auto_match:
        background.add_task(ejecutar_auto_match_cuenta, cuenta.id_cuenta, "tras carga", periodos)

    carga = repo.obtener_carga(id_carga)
    return CargaUploadResponse(
        status="success",
        id_carga=id_carga,
        mensaje=mensaje,
        banco=resultado.banco,
        registros_leidos=carga.total_registros_leidos,
        registros_importados=carga.registros_importados,
        saldo_inicial=resultado.saldo_inicial,
        saldo_final=resultado.saldo_final,
        advertencias=resultado.advertencias,
        auto_match_en_curso=auto_match,
    )


@router.get("/cargas", response_model=List[CargaExtractoResponse], summary="Historial de cargas (paginado)")
def listar_cargas(
    response: Response,
    id_cuenta: Optional[UUID] = None,
    estado: Optional[str] = None,
    fecha_inicio: Optional[date] = None,
    fecha_fin: Optional[date] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db_pg),
):
    filas, total = BancosRepository(db).listar_cargas(id_cuenta, estado, fecha_inicio, fecha_fin, page, limit)
    response.headers["X-Total-Count"] = str(total)
    return filas


@router.get("/cargas/{id_carga}", response_model=CargaExtractoResponse, summary="Detalle de una carga")
def obtener_detalle_carga(id_carga: UUID, db: Session = Depends(get_db_pg)):
    carga = BancosRepository(db).obtener_carga(id_carga)
    if not carga:
        raise HTTPException(status_code=404, detail="La carga especificada no existe.")
    return carga

# -------------------------------------------------------------
# 3. MOVIMIENTOS
# -------------------------------------------------------------

@router.get("/movimientos/pendientes", response_model=List[MovimientoResponse],
            summary="Movimientos bancarios pendientes de conciliar")
def listar_movimientos_pendientes(
    id_cuenta: Optional[UUID] = Query(None, description="ID de la cuenta bancaria"),
    id_institucion: Optional[str] = Query(None, description="ID de la institución (si no se envía id_cuenta)"),
    fecha_inicio: Optional[date] = None,
    fecha_fin: Optional[date] = None,
    limit: int = Query(5000, ge=1, le=20000),
    db: Session = Depends(get_db_pg),
):
    return BancosRepository(db).listar_movimientos_pendientes(
        id_cuenta, id_institucion, fecha_inicio, fecha_fin, limit
    )
