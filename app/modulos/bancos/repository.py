from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, joinedload

from app.modulos.bancos.models import CargaExtracto, CuentaBancaria, Movimiento

TAMANO_LOTE_INSERT = 1000


class BancosRepository:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Cuentas bancarias
    # ------------------------------------------------------------------
    def upsert_cuentas_bancarias_bc(self, cuentas_datos: List[Dict[str, Any]]) -> int:
        cuentas = [
            dict(c) for c in cuentas_datos
            if str(c.get("codigo_banco_bc", "")).strip().upper().startswith("B")
        ]
        if not cuentas:
            return 0

        ahora = datetime.now(ZoneInfo("America/Guayaquil")).replace(tzinfo=None)
        for c in cuentas:
            c["created_at"] = ahora
            c["updated_at"] = ahora
            for campo in ("numero_cuenta", "grupo_registro_bc", "cuenta_contable_bc"):
                if not str(c.get(campo) or "").strip():
                    c[campo] = None

        stmt = pg_insert(CuentaBancaria).values(cuentas)
        # id_institucion y la configuración RPA/Auto-Match no se tocan: los define el usuario
        stmt = stmt.on_conflict_do_update(
            index_elements=["codigo_banco_bc"],
            set_={
                "nombre_cuenta": stmt.excluded.nombre_cuenta,
                "numero_cuenta": stmt.excluded.numero_cuenta,
                "grupo_registro_bc": stmt.excluded.grupo_registro_bc,
                "cuenta_contable_bc": stmt.excluded.cuenta_contable_bc,
                "moneda": stmt.excluded.moneda,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        self.db.execute(stmt)
        self.db.commit()
        return len(cuentas)

    def listar_cuentas_bancarias(self) -> List[CuentaBancaria]:
        return (
            self.db.query(CuentaBancaria)
            .options(joinedload(CuentaBancaria.institucion))
            .filter(CuentaBancaria.activo.is_(True))
            .order_by(CuentaBancaria.numero_cuenta.asc())
            .all()
        )

    def obtener_cuenta_por_id(self, id_cuenta: UUID) -> Optional[CuentaBancaria]:
        return (
            self.db.query(CuentaBancaria)
            .options(joinedload(CuentaBancaria.institucion))
            .filter(CuentaBancaria.id_cuenta == id_cuenta)
            .first()
        )

    # ------------------------------------------------------------------
    # Cargas de extractos
    # ------------------------------------------------------------------
    def crear_carga(self, carga: CargaExtracto) -> CargaExtracto:
        self.db.add(carga)
        self.db.commit()
        return carga

    def listar_cargas(
        self,
        id_cuenta: Optional[UUID] = None,
        estado: Optional[str] = None,
        fecha_inicio: Optional[date] = None,
        fecha_fin: Optional[date] = None,
        page: int = 1,
        limit: int = 50,
    ) -> Tuple[List[CargaExtracto], int]:
        q = self.db.query(CargaExtracto)
        if id_cuenta:
            q = q.filter(CargaExtracto.id_cuenta == id_cuenta)
        if estado:
            q = q.filter(func.lower(CargaExtracto.estado) == estado.lower())
        if fecha_inicio:
            q = q.filter(CargaExtracto.created_at >= fecha_inicio)
        if fecha_fin:
            q = q.filter(func.date(CargaExtracto.created_at) <= fecha_fin)
        total = q.count()
        filas = (
            q.order_by(CargaExtracto.created_at.desc().nulls_last())
            .offset((page - 1) * limit)
            .limit(limit)
            .all()
        )
        return filas, total

    def obtener_carga(self, id_carga: UUID) -> Optional[CargaExtracto]:
        return self.db.get(CargaExtracto, id_carga)

    def verificar_hash_duplicado(self, hash_archivo: str, id_carga: UUID) -> Optional[CargaExtracto]:
        return (
            self.db.query(CargaExtracto)
            .filter(
                CargaExtracto.hash_archivo == hash_archivo,
                CargaExtracto.estado == "Completado",
                CargaExtracto.id_carga != id_carga,
            )
            .first()
        )

    def registrar_error_carga(self, id_carga: UUID, mensaje_error: str) -> None:
        try:
            self.db.rollback()
            carga = self.db.get(CargaExtracto, id_carga)
            if carga:
                carga.estado = "Error"
                carga.mensaje_log = mensaje_error
                self.db.commit()
        except Exception:
            self.db.rollback()

    def actualizar_estado_procesando(self, id_carga: UUID, url_archivo: str, hash_archivo: str) -> None:
        carga = self.db.get(CargaExtracto, id_carga)
        if carga:
            carga.url_archivo = url_archivo
            carga.hash_archivo = hash_archivo
            carga.estado = "Procesando"
            carga.mensaje_log = "Insertando movimientos..."
            self.db.commit()

    def insertar_movimientos_ignorar_duplicados(self, movimientos: List[Dict[str, Any]]) -> int:
        """
        Inserta en lotes ignorando los que ya existen (constraint unq_movimiento_banco).
        NO hace commit: lo hace finalizar_carga_maestro para que todo sea atómico.
        """
        insertados = 0
        for i in range(0, len(movimientos), TAMANO_LOTE_INSERT):
            lote = movimientos[i:i + TAMANO_LOTE_INSERT]
            stmt = (
                pg_insert(Movimiento)
                .values(lote)
                .on_conflict_do_nothing(constraint="unq_movimiento_banco")
            )
            insertados += self.db.execute(stmt).rowcount
        return insertados

    def finalizar_carga_maestro(self, id_carga: UUID, resultado, importados: int, mensaje_final: str) -> None:
        try:
            carga = self.db.get(CargaExtracto, id_carga)
            carga.estado = "Completado"
            carga.total_registros_leidos = resultado.filas_leidas
            carga.registros_importados = importados
            carga.mensaje_log = mensaje_final
            carga.banco_detectado = resultado.banco
            carga.fecha_desde = resultado.fecha_desde
            carga.fecha_hasta = resultado.fecha_hasta
            carga.saldo_inicial = resultado.saldo_inicial
            carga.saldo_final = resultado.saldo_final
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    # ------------------------------------------------------------------
    # Movimientos
    # ------------------------------------------------------------------
    def listar_movimientos_pendientes(
        self,
        id_cuenta: Optional[UUID] = None,
        id_institucion: Optional[str] = None,
        fecha_inicio: Optional[date] = None,
        fecha_fin: Optional[date] = None,
        limit: int = 5000,
    ) -> List[Movimiento]:
        q = self.db.query(Movimiento).filter(Movimiento.estado == "Pendiente")
        if id_cuenta:
            q = q.filter(Movimiento.id_cuenta == id_cuenta)
        elif id_institucion:
            q = q.join(CuentaBancaria, Movimiento.id_cuenta == CuentaBancaria.id_cuenta).filter(
                CuentaBancaria.id_institucion == id_institucion
            )
        if fecha_inicio:
            q = q.filter(Movimiento.fecha_transaccion >= fecha_inicio)
        if fecha_fin:
            q = q.filter(Movimiento.fecha_transaccion <= fecha_fin)
        return q.order_by(Movimiento.fecha_transaccion.desc()).limit(limit).all()
