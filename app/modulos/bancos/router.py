import uuid
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, status, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db_pg
from app.modulos.bancos.schemas import (
    CargaExtractoResponse, 
    MovimientoResponse, 
    CuentaBancariaResponse, 
    CuentaBancariaUpdate
)
from app.modulos.bancos.service import BancosService
from app.modulos.bancos.models import CargaExtracto, Movimiento, CuentaBancaria
from app.modulos.bancos.bc_service import BancosBCService
from app.modulos.bancos.repository import BancosRepository

router = APIRouter(prefix="/api/v1/bancos", tags=["Bancos"])

# -------------------------------------------------------------
# 1. RUTAS ESTÁTICAS DE CUENTAS BANCARIAS
# -------------------------------------------------------------

@router.post("/cuentas/sincronizar", summary="Sincronizar Catálogo de Cuentas Bancarias de BC")
def sincronizar_cuentas_bc(db: Session = Depends(get_db_pg)):
    """
    Sincroniza el catálogo de cuentas bancarias operativas desde Business Central hacia PostgreSQL.
    """
    cuentas_bc, error = BancosBCService.sincronizar_catalogo_cuentas_bc()
    if error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=error)

    repo = BancosRepository(db)
    sincronizados = repo.upsert_cuentas_bancarias_bc(cuentas_bc)

    return {
        "status": "success",
        "total_registros_bc": len(cuentas_bc),
        "total_procesados": sincronizados,
        "data": cuentas_bc
    }


@router.get("/cuentas", response_model=List[CuentaBancariaResponse], summary="Listar catálogo de cuentas bancarias operativas")
def listar_cuentas_bancarias(db: Session = Depends(get_db_pg)):
    repo = BancosRepository(db)
    return repo.listar_cuentas_bancarias()


@router.put("/cuentas/{id_cuenta}", response_model=CuentaBancariaResponse, summary="Actualizar configuración de cuenta bancaria (RPA / Inst)")
def actualizar_cuenta_bancaria(
    id_cuenta: UUID, 
    payload: CuentaBancariaUpdate, 
    db: Session = Depends(get_db_pg)
):
    repo = BancosRepository(db)
    cuenta = repo.obtener_cuenta_por_id(id_cuenta)
    if not cuenta:
        raise HTTPException(status_code=404, detail="La cuenta bancaria especificada no existe.")

    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(cuenta, key, value)

    db.commit()
    db.refresh(cuenta)
    return cuenta

# -------------------------------------------------------------
# 2. RUTAS DE CARGA DE EXTRACTOS Y MOVIMIENTOS
# -------------------------------------------------------------

@router.post(
    "/cargas/upload", 
    response_model=dict, 
    status_code=status.HTTP_201_CREATED,
    summary="Subir y procesar extracto bancario (ETL)"
)
async def cargar_archivo_bancario(
    id_cuenta: UUID = Form(..., description="ID de la cuenta bancaria operativa"),
    created_by: str = Form(..., description="Usuario que ejecuta la carga"),
    archivo: UploadFile = File(..., description="Archivo Excel o CSV del extracto"),
    db: Session = Depends(get_db_pg)
):
    repo = BancosRepository(db)
    cuenta = repo.obtener_cuenta_por_id(id_cuenta)
    if not cuenta:
        raise HTTPException(status_code=404, detail="La cuenta bancaria especificada no existe.")

    id_carga_nueva = uuid.uuid4()
    carga_maestro = CargaExtracto(
        id_carga=id_carga_nueva,
        id_cuenta=id_cuenta,
        id_institucion=cuenta.id_institucion,
        nombre_archivo=archivo.filename,
        created_by=created_by,
        estado="Iniciado"
    )
    db.add(carga_maestro)
    db.commit()

    file_bytes = await archivo.read()
    service = BancosService(db)
    
    exito, mensaje = await service.procesar_archivo_bancos_service(
        file_bytes=file_bytes,
        id_carga=id_carga_nueva,
        id_cuenta=id_cuenta,
        filename_original=archivo.filename,
        usuario_carga=created_by,
        nombre_banco_bd=cuenta.grupo_registro_bc or cuenta.nombre_cuenta
    )

    if not exito:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, 
            detail={"id_carga": str(id_carga_nueva), "error": mensaje}
        )

    return {
        "status": "success",
        "id_carga": id_carga_nueva,
        "mensaje": mensaje
    }


@router.get("/cargas", response_model=List[CargaExtractoResponse], summary="Listar historial de cargas")
def listar_cargas(limit: int = 50, db: Session = Depends(get_db_pg)):
    return db.query(CargaExtracto).order_by(CargaExtracto.created_at.desc()).limit(limit).all()


@router.get("/movimientos/pendientes", response_model=List[MovimientoResponse], summary="Listar movimientos pendientes")
def listar_movimientos_pendientes(
    id_cuenta: Optional[UUID] = Query(None, description="ID de la cuenta bancaria"),
    id_institucion: Optional[str] = Query(None, description="ID de la institución bancaria"),
    db: Session = Depends(get_db_pg)
):
    query = db.query(Movimiento).filter(Movimiento.estado == "Pendiente")
    if id_cuenta:
        query = query.filter(Movimiento.id_cuenta == id_cuenta)
    elif id_institucion:
        query = query.filter(Movimiento.id_institucion == id_institucion)
    return query.order_by(Movimiento.fecha_transaccion.desc()).all()


@router.get("/cargas/{id_carga}", response_model=CargaExtractoResponse, summary="Obtener detalle de una carga")
def obtener_detalle_carga(id_carga: UUID, db: Session = Depends(get_db_pg)):
    carga = db.query(CargaExtracto).filter(CargaExtracto.id_carga == id_carga).first()
    if not carga:
        raise HTTPException(status_code=404, detail="La carga especificada no existe.")
    return carga