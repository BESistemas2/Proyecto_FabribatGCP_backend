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

    def upsert_movimientos_erp_espejo(
        self, 
        raw_bc_entries: List[Dict[str, Any]], 
        grupo_registro_bc: str
    ) -> Dict[str, Any]:
        """
        Sincroniza los movimientos de BC en PostgreSQL mediante Multi-Row Bulk Processing.
        """
        if not raw_bc_entries:
            return {"nuevos": 0, "modificados": 0, "requiere_rematch": False}

        # 1. Consultar estado actual en BD para comparar en memoria
        entry_nos = [item["entry_no_bc"] for item in raw_bc_entries if item.get("entry_no_bc")]
        
        sql_existentes = text("""
            SELECT entry_no_bc, monto, posting_date::text AS posting_date, document_no, external_document_no
            FROM bancos.movimientos_erp
            WHERE entry_no_bc = ANY(:entries);
        """)
        
        res = self.db.execute(sql_existentes, {"entries": entry_nos}).fetchall()
        existentes_map = {r.entry_no_bc: dict(r._mapping) for r in res}

        nuevos = 0
        modificados = 0

        # 2. Clasificación ultrarrápida en MEMORIA (< 2 milisegundos)
        for item in raw_bc_entries:
            entry_id = item.get("entry_no_bc")
            if entry_id not in existentes_map:
                nuevos += 1
            else:
                prev = existentes_map[entry_id]
                monto_cambio = abs(float(prev["monto"] or 0) - float(item.get("monto") or 0)) > 0.001
                fecha_cambio = str(prev["posting_date"]) != str(item.get("posting_date"))
                doc_cambio = (prev["document_no"] or "") != (item.get("document_no") or "")
                ext_cambio = (prev["external_document_no"] or "") != (item.get("external_document_no") or "")
                
                if monto_cambio or fecha_cambio or doc_cambio or ext_cambio:
                    modificados += 1

        # 3. UPSERT MASIVO EN BLOQUES (Multi-Row VALUES)
        batch_size = 500
        for i in range(0, len(raw_bc_entries), batch_size):
            chunk = raw_bc_entries[i:i + batch_size]
            
            value_clauses = []
            params = {}
            for idx, item in enumerate(chunk):
                value_clauses.append(f"""(
                    :entry_no_{idx}, :codigo_banco_{idx}, :grupo_registro_{idx}, :posting_date_{idx},
                    :document_no_{idx}, :external_document_{idx}, :description_{idx}, :debit_{idx},
                    :credit_{idx}, :monto_{idx}, CURRENT_TIMESTAMP
                )""")
                params[f"entry_no_{idx}"] = item.get("entry_no_bc")
                params[f"codigo_banco_{idx}"] = item.get("codigo_banco_bc", "")
                params[f"grupo_registro_{idx}"] = item.get("grupo_registro_bc", "")
                params[f"posting_date_{idx}"] = item.get("posting_date")
                params[f"document_no_{idx}"] = item.get("document_no", "")
                params[f"external_document_{idx}"] = item.get("external_document_no", "")
                params[f"description_{idx}"] = item.get("description", "")
                params[f"debit_{idx}"] = item.get("debit_amount", 0.0)
                params[f"credit_{idx}"] = item.get("credit_amount", 0.0)
                params[f"monto_{idx}"] = item.get("monto", 0.0)

            sql_upsert_chunk = text(f"""
                INSERT INTO bancos.movimientos_erp (
                    entry_no_bc, codigo_banco_bc, grupo_registro_bc, posting_date,
                    document_no, external_document_no, description, debit_amount,
                    credit_amount, monto, updated_at
                ) VALUES {', '.join(value_clauses)}
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
            self.db.execute(sql_upsert_chunk, params)

        self.db.commit()

        requiere_rematch = (nuevos > 0) or (modificados > 0)

        return {
            "nuevos": nuevos,
            "modificados": modificados,
            "total_sincronizados": len(raw_bc_entries),
            "requiere_rematch": requiere_rematch
        }

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

    def obtener_movimientos_erp_pendientes(
        self, 
        grupo_bc: str, 
        fecha_inicio: date, 
        fecha_fin: date
    ) -> List[Dict[str, Any]]:
        """
        Consulta en PostgreSQL los movimientos del ERP que continúan PENDIENTES / NO CONCILIADOS.
        """
        sql = text("""
            SELECT 
                id_movimiento_erp, 
                entry_no_bc, 
                posting_date::text AS posting_date, 
                document_no,
                COALESCE(external_document_no, '') AS external_document_no, 
                description, 
                monto,
                COALESCE(estado, 'NO CONCILIADO') AS estado,
                regla_cruce
            FROM bancos.movimientos_erp
            WHERE (grupo_registro_bc = :grupo_bc OR codigo_banco_bc = :grupo_bc)
              AND posting_date BETWEEN :f_ini AND :f_fin
              AND (estado IN ('NO CONCILIADO', 'Pendiente') OR estado IS NULL)
            ORDER BY posting_date ASC;
        """)
        
        res = self.db.execute(
            sql, 
            {"grupo_bc": grupo_bc.strip(), "f_ini": fecha_inicio, "f_fin": fecha_fin}
        ).fetchall()
        
        movs = []
        for r in res:
            d = dict(r._mapping)
            d["monto"] = float(d["monto"]) if d.get("monto") is not None else 0.0
            movs.append(d)
        return movs

    def aplicar_actualizaciones_conciliacion(self, banco_updates: List[Dict[str, Any]], erp_updates: List[Dict[str, Any]]) -> None:
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
    def obtener_grupos_conciliados(
        self, 
        id_cuenta: str, 
        fecha_inicio: str, 
        fecha_fin: str
    ) -> List[Dict[str, Any]]:
        """
        Consulta los pares de movimientos conciliados (Banco vs ERP)
        agrupando correctamente las relaciones 1:N bajo un mismo movimiento bancario.
        """
        f_ini_dt = datetime.strptime(str(fecha_inicio).split("T")[0], "%Y-%m-%d").date()
        f_fin_dt = datetime.strptime(str(fecha_fin).split("T")[0], "%Y-%m-%d").date()

        sql = text("""
            SELECT 
                b.id_movimiento AS banco_id,
                DATE(COALESCE(a.created_at, e.updated_at, b.fecha_transaccion))::text AS fecha_conciliacion,
                COALESCE(b.regla_cruce, e.regla_cruce, 'MANUAL') AS regla_aplicada,
                COALESCE(a.creado_por, 'Sistema') AS creado_por,
                b.fecha_transaccion::text AS banco_fecha,
                b.concepto AS banco_concepto,
                b.numero_referencia AS banco_referencia,
                b.monto AS banco_monto,
                COALESCE(b.tipo_movimiento, 'CREDITO') AS banco_tipo,
                e.id_movimiento_erp AS erp_id,
                e.document_no AS erp_documento,
                COALESCE(e.external_document_no, '') AS erp_ext_documento,
                e.posting_date::text AS erp_fecha,
                e.description AS erp_concepto,
                e.monto AS erp_monto,
                e.grupo_registro_bc AS erp_cuenta
            FROM bancos.movimientos b
            INNER JOIN bancos.movimientos_erp e ON e.id_movimiento_banco = b.id_movimiento
            LEFT JOIN conciliacion.actas a ON b.id_acta = a.id_acta
            WHERE b.id_cuenta = :id_cuenta
              AND CAST(b.fecha_transaccion AS DATE) BETWEEN :f_ini AND :f_fin
              AND (b.estado LIKE 'CONCILIADO%' OR b.estado = 'CONCILIADO_MANUAL')
            ORDER BY b.fecha_transaccion DESC;
        """)
        
        res = self.db.execute(
            sql, 
            {"id_cuenta": id_cuenta, "f_ini": f_ini_dt, "f_fin": f_fin_dt}
        ).mappings().all()

        # Agrupamiento por ID de Banco para soportar relaciones 1:N
        grupos_dict: Dict[str, Dict[str, Any]] = {}

        for r in res:
            banco_id = str(r["banco_id"])

            if banco_id not in grupos_dict:
                grupos_dict[banco_id] = {
                    "id_conciliacion": banco_id,
                    "fecha_conciliacion": r["fecha_conciliacion"],
                    "regla_aplicada": r["regla_aplicada"],
                    "creado_por": r["creado_por"],
                    "banco": {
                        "id_movimiento_banco": banco_id,
                        "fecha": r["banco_fecha"],
                        "concepto": r["banco_concepto"] or "Movimiento Banco",
                        "referencia": r["banco_referencia"] or "S/R",
                        "monto": float(r["banco_monto"] or 0),
                        "tipo": str(r["banco_tipo"]).upper()
                    },
                    "partidas_bc": []
                }

            # Acumular todos los asientos contables de BC vinculados
            if r["erp_id"]:
                grupos_dict[banco_id]["partidas_bc"].append({
                    "id_movimiento_bc": str(r["erp_id"]),
                    "num_documento": r["erp_documento"] or "DOC-BC",
                    "external_document_no": r["erp_ext_documento"] or "",
                    "fecha": r["erp_fecha"],
                    "concepto": r["erp_concepto"] or "Asiento Contable",
                    "monto": float(r["erp_monto"] or 0),
                    "cuenta_contable": r["erp_cuenta"] or ""
                })

        return list(grupos_dict.values())