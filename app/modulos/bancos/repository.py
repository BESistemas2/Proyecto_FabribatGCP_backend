from datetime import datetime
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional
from uuid import UUID
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.modulos.bancos.models import CargaExtracto, Movimiento, CuentaBancaria


class BancosRepository:
    def __init__(self, db: Session):
        self.db = db

    def upsert_cuentas_bancarias_bc(self, cuentas_datos: List[Dict[str, Any]]) -> int:
        if not cuentas_datos:
            return 0

        # 1. Filtrar únicamente códigos de banco que empiecen por 'B'
        cuentas_filtradas = [
            c for c in cuentas_datos 
            if str(c.get("codigo_banco_bc", "")).strip().upper().startswith("B")
        ]

        if not cuentas_filtradas:
            return 0

        # Zona horaria de Ecuador (UTC-5)
        tz_ec = ZoneInfo("America/Guayaquil")
        ahora_ec_naive = datetime.now(tz_ec).replace(tzinfo=None)

        for c in cuentas_filtradas:
            c["created_at"] = ahora_ec_naive
            c["updated_at"] = ahora_ec_naive
            
            # Convertir valores vacíos a None (NULL en PostgreSQL)
            for campo in ["numero_cuenta", "grupo_registro_bc", "cuenta_contable_bc"]:
                if campo in c and not str(c.get(campo) or "").strip():
                    c[campo] = None

        # 2. Upsert masivo en PostgreSQL
        stmt = pg_insert(CuentaBancaria).values(cuentas_filtradas)
        
        # Se omite id_institucion para no borrar la vinculación manual que haga el usuario
        update_cols = {
            "nombre_cuenta": stmt.excluded.nombre_cuenta,
            "numero_cuenta": stmt.excluded.numero_cuenta,
            "grupo_registro_bc": stmt.excluded.grupo_registro_bc,
            "cuenta_contable_bc": stmt.excluded.cuenta_contable_bc,
            "moneda": stmt.excluded.moneda,
            "updated_at": stmt.excluded.updated_at,
        }

        sql_upsert = stmt.on_conflict_do_update(
            index_elements=["codigo_banco_bc"],
            set_=update_cols
        )

        self.db.execute(sql_upsert)
        self.db.commit()
        return len(cuentas_filtradas)

    def listar_cuentas_bancarias(self) -> List[CuentaBancaria]:
        return self.db.query(CuentaBancaria).filter(CuentaBancaria.activo == True).all()

    def obtener_cuenta_por_id(self, id_cuenta: UUID) -> Optional[CuentaBancaria]:
        return self.db.query(CuentaBancaria).filter(CuentaBancaria.id_cuenta == id_cuenta).first()

    def verificar_hash_duplicado(self, hash_archivo: str, id_carga: UUID) -> Optional[CargaExtracto]:
        return self.db.query(CargaExtracto).filter(
            CargaExtracto.hash_archivo == hash_archivo,
            CargaExtracto.estado == 'Completado',
            CargaExtracto.id_carga != id_carga
        ).first()

    def registrar_error_carga(self, id_carga: UUID, mensaje_error: str) -> None:
        try:
            self.db.rollback()
            carga = self.db.query(CargaExtracto).filter(CargaExtracto.id_carga == id_carga).first()
            if carga:
                carga.estado = 'Error'
                carga.mensaje_log = mensaje_error
                self.db.commit()
        except Exception:
            self.db.rollback()

    def actualizar_estado_procesando(self, id_carga: UUID, url_archivo: str, hash_archivo: str) -> None:
        try:
            carga = self.db.query(CargaExtracto).filter(CargaExtracto.id_carga == id_carga).first()
            if carga:
                carga.url_archivo = url_archivo
                carga.hash_archivo = hash_archivo
                carga.estado = 'Procesando'
                carga.mensaje_log = 'Iniciando lectura de Pandas...'
                self.db.commit()
        except Exception as e:
            self.db.rollback()
            raise e

    def insertar_movimientos_ignorar_duplicados(self, movimientos_datos: List[Dict[str, Any]]) -> int:
        if not movimientos_datos:
            return 0
        try:
            stmt = (
                pg_insert(Movimiento)
                .values(movimientos_datos)
                .on_conflict_do_nothing(constraint='unq_movimiento_banco')
            )
            result = self.db.execute(stmt)
            self.db.commit()
            return result.rowcount
        except Exception as e:
            self.db.rollback()
            raise e

    def finalizar_carga_maestro(self, id_carga: UUID, total_leidos: int, importados: int, mensaje_final: str) -> None:
        try:
            carga = self.db.query(CargaExtracto).filter(CargaExtracto.id_carga == id_carga).first()
            if carga:
                carga.estado = 'Completado'
                carga.total_registros_leidos = total_leidos
                carga.registros_importados = importados
                carga.mensaje_log = mensaje_final
                self.db.commit()
        except Exception as e:
            self.db.rollback()
            raise e