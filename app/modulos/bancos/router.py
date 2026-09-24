import uuid
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, UploadFile, File, Form, Query, status, HTTPException
from sqlalchemy.orm import Session

import io
from datetime import datetime
import pandas as pd
from sqlalchemy import text

from app.core.database import get_db_pg
from app.core.drive_utils import extraer_drive_file_id, descargar_desde_google_drive
from app.modulos.bancos.schemas import CargaAppSheetRequest

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

@router.post(
    "/upload-appsheet",
    status_code=status.HTTP_201_CREATED,
    summary="Cargar extracto bancario desde Webhook de AppSheet (vía Google Drive)"
)
def upload_extracto_appsheet(
    payload: CargaAppSheetRequest,
    db: Session = Depends(get_db_pg)
):
    print(f"\n🚀 [AppSheet Webhook] Solicitud recibida | Cuenta: {payload.id_cuenta} | Ruta/ID: '{payload.rutaArchivo}'")

    # 1. Verificar existencia de la cuenta bancaria
    sql_cta = text("SELECT id_cuenta, id_institucion, nombre_cuenta FROM bancos.cuentas_bancarias WHERE id_cuenta = :id LIMIT 1;")
    cuenta = db.execute(sql_cta, {"id": payload.id_cuenta}).fetchone()
    if not cuenta:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail=f"La cuenta bancaria con ID '{payload.id_cuenta}' no existe."
        )

    # 2. Obtener File ID de Google Drive
    file_id = extraer_drive_file_id(payload.rutaArchivo)
    if not file_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se pudo extraer un ID válido de Google Drive desde el campo 'rutaArchivo'."
        )

    # 3. Descargar el archivo desde Google Drive
    try:
        content_bytes, nombre_archivo = descargar_desde_google_drive(file_id)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Error al descargar el archivo desde Google Drive: {str(e)}"
        )

    # 4. Procesar el archivo Excel / CSV en Pandas
    try:
        if nombre_archivo.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(content_bytes))
        else:
            df = pd.read_excel(io.BytesIO(content_bytes))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Error al parsear el archivo Excel/CSV: {str(e)}"
        )

    if df.empty:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo cargado no contiene registros."
        )

    # 5. Insertar Registro de Carga Maestro en bancos.cargas_extractos
    id_carga = uuid.uuid4()
    total_registros = len(df)
    
    sql_carga = text("""
        INSERT INTO bancos.cargas_extractos (
            id_carga, id_cuenta, id_institucion, nombre_archivo, url_archivo,
            total_registros_leidos, registros_importados, estado, created_by, created_at, updated_at
        ) VALUES (
            :id_carga, :id_cuenta, :id_institucion, :nombre_archivo, :url_archivo,
            :total_registros, :registros_importados, 'PROCESADO', :created_by, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        );
    """)
    
    db.execute(sql_carga, {
        "id_carga": id_carga,
        "id_cuenta": cuenta.id_cuenta,
        "id_institucion": cuenta.id_institucion,
        "nombre_archivo": nombre_archivo,
        "url_archivo": f"google_drive://{file_id}",
        "total_registros": total_registros,
        "registros_importados": total_registros,
        "created_by": payload.usuarioCarga
    })

    # 6. Insertar los movimientos del extracto en bancos.movimientos
    # (Adaptado a las columnas estándar del DataFrame: fecha, referencia, concepto, monto)
    movimientos_insert = []
    for _, row in df.iterrows():
        movimientos_insert.append({
            "id_movimiento": uuid.uuid4(),
            "id_carga": id_carga,
            "id_cuenta": cuenta.id_cuenta,
            "fecha_transaccion": str(row.get('fecha_transaccion') or row.get('fecha') or datetime.now().date()),
            "numero_referencia": str(row.get('numero_referencia') or row.get('referencia') or ''),
            "concepto": str(row.get('concepto') or row.get('descripcion') or ''),
            "monto": float(row.get('monto') or row.get('valor') or 0.0),
            "tipo_movimiento": str(row.get('tipo_movimiento') or 'TRANSFERENCIA'),
            "estado": "Pendiente"
        })

    if movimientos_insert:
        sql_mov = text("""
            INSERT INTO bancos.movimientos (
                id_movimiento, id_carga, id_cuenta, fecha_transaccion,
                numero_referencia, concepto, monto, tipo_movimiento, estado
            ) VALUES (
                :id_movimiento, :id_carga, :id_cuenta, :fecha_transaccion,
                :numero_referencia, :concepto, :monto, :tipo_movimiento, :estado
            );
        """)
        db.execute(sql_mov, movimientos_insert)

    db.commit()
    print(f"✅ [AppSheet Webhook] Carga completada exitosamente. ID Carga: {id_carga} | {total_registros} movimientos importados.")

    return {
        "status": "success",
        "message": "Extracto bancario cargado y procesado exitosamente desde AppSheet",
        "data": {
            "idCarga": str(id_carga),
            "idCuenta": str(cuenta.id_cuenta),
            "nombreArchivo": nombre_archivo,
            "totalRegistrosLeidos": total_registros,
            "registrosImportados": total_registros
        }
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