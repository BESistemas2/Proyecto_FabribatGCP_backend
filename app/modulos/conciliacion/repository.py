# app/modulos/conciliacion/repository.py
from datetime import datetime, date
from typing import List, Dict, Any, Optional
from uuid import UUID
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modulos.conciliacion.models import Acta, PartidaTransito
from app.modulos.bancos.models import CuentaBancaria, Movimiento


class ConciliacionRepository:
    def __init__(self, db: Session):
        self.db = db

    def obtener_cuenta_bancaria(self, id_cuenta: Any) -> Optional[CuentaBancaria]:
        return self.db.query(CuentaBancaria).filter(CuentaBancaria.id_cuenta == id_cuenta).first()

    def upsert_movimientos_erp_espejo(self, raw_bc_entries: List[Dict[str, Any]], grupo_registro_bc: str) -> int:
        if not raw_bc_entries:
            return 0

        sql_upsert = text("""
            INSERT INTO bancos.movimientos_erp (
                entry_no_bc, codigo_banco_bc, grupo_registro_bc, posting_date,
                document_no, external_document_no, description, debit_amount,
                credit_amount, monto, updated_at
            ) VALUES (
                :entryNo, :bankAccountNo, :bankAccPostingGroup, :postingDate,
                :documentNo, :externalDocumentNo, :description, :debitAmount,
                :creditAmount, :amount, CURRENT_TIMESTAMP
            )
            ON CONFLICT (entry_no_bc) DO UPDATE SET
                posting_date = EXCLUDED.posting_date,
                document_no = EXCLUDED.document_no,
                external_document_no = EXCLUDED.external_document_no,
                description = EXCLUDED.description,
                debit_amount = EXCLUDED.debit_amount,
                credit_amount = EXCLUDED.credit_amount,
                monto = EXCLUDED.monto,
                updated_at = CURRENT_TIMESTAMP;
        """)

        for item in raw_bc_entries:
            self.db.execute(sql_upsert, item)

        self.db.commit()
        return len(raw_bc_entries)

    def obtener_movimientos_banco_pendientes(self, id_cuenta: UUID, fecha_inicio: date, fecha_fin: date) -> List[Dict[str, Any]]:
        sql = text("""
            SELECT id_movimiento, fecha_transaccion::text, numero_referencia, concepto, monto
            FROM bancos.movimientos
            WHERE id_cuenta = :id_cuenta
              AND fecha_transaccion BETWEEN :f_ini AND :f_fin
              AND estado IN ('NO CONCILIADO', 'Pendiente')
            ORDER BY fecha_transaccion ASC;
        """)
        res = self.db.execute(sql, {"id_cuenta": id_cuenta, "f_ini": fecha_inicio, "f_fin": fecha_fin}).fetchall()
        movs = []
        for r in res:
            d = dict(r._mapping)
            d["monto"] = float(d["monto"]) if d.get("monto") is not None else 0.0
            movs.append(d)
        return movs

    def obtener_movimientos_erp_pendientes(self, grupo_registro_bc: str, fecha_inicio: date, fecha_fin: date) -> List[Dict[str, Any]]:
        sql = text("""
            SELECT id_movimiento_erp, entry_no_bc, posting_date::text, document_no,
                   COALESCE(external_document_no, '') AS external_document_no, description, monto
            FROM bancos.movimientos_erp
            WHERE grupo_registro_bc = :grupo_bc
              AND posting_date BETWEEN :f_ini AND :f_fin
              AND estado = 'NO CONCILIADO'
            ORDER BY posting_date ASC;
        """)
        res = self.db.execute(sql, {"grupo_bc": grupo_registro_bc, "f_ini": fecha_inicio, "f_fin": fecha_fin}).fetchall()
        movs = []
        for r in res:
            d = dict(r._mapping)
            d["monto"] = float(d["monto"]) if d.get("monto") is not None else 0.0
            movs.append(d)
        return movs

    def aplicar_actualizaciones_conciliacion(self, banco_updates: List[Dict[str, Any]], erp_updates: List[Dict[str, Any]]) -> None:
        """Actualiza masivamente por lotes los estados y vinculaciones en PostgreSQL."""
        try:
            if banco_updates:
                sql_banco = text("""
                    UPDATE bancos.movimientos
                    SET estado = :estado, id_acta = :id_acta, regla_cruce = :regla_cruce
                    WHERE id_movimiento = :id_movimiento;
                """)
                self.db.execute(sql_banco, banco_updates)

            if erp_updates:
                sql_erp = text("""
                    UPDATE bancos.movimientos_erp
                    SET estado = :estado, id_movimiento_banco = :id_movimiento_banco, 
                        id_acta = :id_acta, regla_cruce = :regla_cruce
                    WHERE id_movimiento_erp = :id_movimiento_erp;
                """)
                self.db.execute(sql_erp, erp_updates)

            self.db.commit()
        except Exception as e:
            self.db.rollback()
            raise RuntimeError(f"Error al actualizar marcas de conciliación: {str(e)}")

    def registrar_acta_maestro(
        self, id_acta: UUID, id_cuenta: UUID, cuenta_contable: str, periodo: str,
        saldo_banco: float, saldo_libros: float, total_conciliado: float, creado_por: str
    ) -> UUID:
        """Registra una nueva acta o actualiza la existente si ya fue creada para el período."""
        try:
            acta_existente = self.db.query(Acta).filter(
                Acta.cuenta_contable == cuenta_contable,
                Acta.periodo == periodo
            ).first()

            if acta_existente:
                acta_existente.id_cuenta = id_cuenta
                acta_existente.saldo_banco = saldo_banco
                acta_existente.saldo_libros = saldo_libros
                acta_existente.total_conciliado = total_conciliado
                acta_existente.creado_por = creado_por
                self.db.commit()
                return acta_existente.id_acta
            else:
                acta = Acta(
                    id_acta=id_acta,
                    id_cuenta=id_cuenta,
                    cuenta_contable=cuenta_contable,
                    periodo=periodo,
                    saldo_banco=saldo_banco,
                    saldo_libros=saldo_libros,
                    total_conciliado=total_conciliado,
                    creado_por=creado_por,
                    estado='BORRADOR'
                )
                self.db.add(acta)
                self.db.commit()
                return id_acta
        except Exception as e:
            self.db.rollback()
            raise RuntimeError(f"Error al registrar Acta: {str(e)}")

    def listar_actas(self, limit: int = 50) -> List[Acta]:
        return self.db.query(Acta).order_by(Acta.created_at.desc()).limit(limit).all()

    def obtener_acta_por_id(self, id_acta: UUID) -> Optional[Acta]:
        return self.db.query(Acta).filter(Acta.id_acta == id_acta).first()