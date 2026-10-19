from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, selectinload

from app.modulos.bancos.models import CargaExtracto, CuentaBancaria, Movimiento, MovimientoERP
from app.modulos.conciliacion.models import Acta, PartidaTransito

ESTADOS_PENDIENTE = ("Pendiente", "NO CONCILIADO")
TAMANO_LOTE = 500


class ConciliacionRepository:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Cuentas
    # ------------------------------------------------------------------
    def obtener_cuenta_bancaria(self, id_cuenta: UUID) -> Optional[CuentaBancaria]:
        return self.db.get(CuentaBancaria, id_cuenta)

    def resolver_cuenta(self, cuenta_contable: str) -> Optional[CuentaBancaria]:
        """Cuenta bancaria a partir de la cuenta contable, el código de banco o el grupo de BC."""
        valor = (cuenta_contable or "").strip()
        for columna in (CuentaBancaria.cuenta_contable_bc, CuentaBancaria.codigo_banco_bc,
                        CuentaBancaria.grupo_registro_bc):
            cuentas = self.db.query(CuentaBancaria).filter(columna == valor).limit(2).all()
            if len(cuentas) == 1:
                return cuentas[0]
        return None

    # ------------------------------------------------------------------
    # Espejo del ERP (Business Central)
    # ------------------------------------------------------------------
    def upsert_movimientos_erp_espejo(self, entradas: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Inserta/actualiza el espejo local de BC. No toca estado ni vínculos de conciliación.
        NO hace commit (lo hace el servicio al final de la operación).
        """
        if not entradas:
            return {"nuevos": 0, "modificados": 0, "total_sincronizados": 0, "requiere_rematch": False}

        # Un EntryNo repetido en el mismo lote rompe el ON CONFLICT: se deja el último
        unicos = {e["entry_no_bc"]: e for e in entradas}
        filas = list(unicos.values())

        previos = {
            r.entry_no_bc: r for r in self.db.execute(
                select(MovimientoERP.entry_no_bc, MovimientoERP.monto, MovimientoERP.posting_date,
                       MovimientoERP.document_no, MovimientoERP.external_document_no)
                .where(MovimientoERP.entry_no_bc.in_(list(unicos)))
            )
        }
        nuevos = modificados = 0
        for e in filas:
            p = previos.get(e["entry_no_bc"])
            if p is None:
                nuevos += 1
            elif (Decimal(p.monto or 0) != e["monto"] or str(p.posting_date) != e["posting_date"]
                  or (p.document_no or "") != e["document_no"]
                  or (p.external_document_no or "") != e["external_document_no"]):
                modificados += 1

        for i in range(0, len(filas), TAMANO_LOTE):
            stmt = pg_insert(MovimientoERP).values(filas[i:i + TAMANO_LOTE])
            stmt = stmt.on_conflict_do_update(
                index_elements=["entry_no_bc"],
                set_={c: stmt.excluded[c] for c in (
                    "codigo_banco_bc", "grupo_registro_bc", "posting_date", "document_no",
                    "external_document_no", "description", "debit_amount", "credit_amount", "monto")}
                | {"updated_at": func.now()},
            )
            self.db.execute(stmt)
        self.db.flush()
        return {"nuevos": nuevos, "modificados": modificados, "total_sincronizados": len(filas),
                "requiere_rematch": bool(nuevos or modificados)}

    # ------------------------------------------------------------------
    # Pendientes
    # ------------------------------------------------------------------
    def movimientos_banco_pendientes(self, id_cuenta: UUID, f_ini: date, f_fin: date) -> List[Movimiento]:
        return (
            self.db.query(Movimiento)
            .filter(Movimiento.id_cuenta == id_cuenta,
                    Movimiento.fecha_transaccion.between(f_ini, f_fin),
                    Movimiento.estado.in_(ESTADOS_PENDIENTE))
            .order_by(Movimiento.fecha_transaccion)
            .all()
        )

    def movimientos_erp_pendientes(self, codigo_banco_bc: str, f_ini: date, f_fin: date) -> List[MovimientoERP]:
        return (
            self.db.query(MovimientoERP)
            .filter(MovimientoERP.codigo_banco_bc == codigo_banco_bc,
                    MovimientoERP.posting_date.between(f_ini, f_fin),
                    (MovimientoERP.estado.in_(ESTADOS_PENDIENTE)) | (MovimientoERP.estado.is_(None)))
            .order_by(MovimientoERP.posting_date)
            .all()
        )

    def totales_periodo(self, id_cuenta: UUID, codigo_banco_bc: str, f_ini: date, f_fin: date) -> Dict[str, Decimal]:
        """Totales del periodo (todos los estados) para el resumen del acta."""
        cero = Decimal("0")
        banco = self.db.execute(
            select(
                func.coalesce(func.sum(Movimiento.monto).filter(Movimiento.monto > 0), 0),
                func.coalesce(func.sum(Movimiento.monto).filter(Movimiento.monto < 0), 0),
                func.coalesce(func.sum(Movimiento.monto).filter(~Movimiento.estado.in_(ESTADOS_PENDIENTE)), 0),
            ).where(Movimiento.id_cuenta == id_cuenta, Movimiento.fecha_transaccion.between(f_ini, f_fin))
        ).one()
        libros = self.db.execute(
            select(func.coalesce(func.sum(MovimientoERP.monto), 0))
            .where(MovimientoERP.codigo_banco_bc == codigo_banco_bc,
                   MovimientoERP.posting_date.between(f_ini, f_fin))
        ).scalar_one()
        return {
            "creditos_banco": Decimal(banco[0] or cero),
            "debitos_banco": Decimal(banco[1] or cero),
            "conciliado_banco": Decimal(banco[2] or cero),
            "neto_libros": Decimal(libros or cero),
        }

    def saldos_extracto(self, id_cuenta: UUID, f_ini: date, f_fin: date) -> Dict[str, Any]:
        """
        Saldos informados por el banco: el inicial del primer extracto cargado del periodo
        y el final del último (con la fecha a la que corresponde cada uno).
        """
        base = self.db.query(CargaExtracto).filter(
            CargaExtracto.id_cuenta == id_cuenta, CargaExtracto.estado == "Completado",
            CargaExtracto.fecha_desde.isnot(None), CargaExtracto.saldo_inicial.isnot(None),
            CargaExtracto.fecha_desde <= f_fin, CargaExtracto.fecha_hasta >= f_ini,
        )
        primera = base.order_by(CargaExtracto.fecha_desde.asc(), CargaExtracto.created_at.desc()).first()
        ultima = base.order_by(CargaExtracto.fecha_hasta.desc(), CargaExtracto.created_at.desc()).first()
        return {
            "saldo_inicial": primera.saldo_inicial if primera else None,
            "fecha_saldo_inicial": primera.fecha_desde if primera else None,
            "saldo_final": ultima.saldo_final if ultima else None,
            "fecha_saldo_final": ultima.fecha_hasta if ultima else None,
        }

    # ------------------------------------------------------------------
    # Marcas de conciliación
    # ------------------------------------------------------------------
    def aplicar_actualizaciones_conciliacion(self, banco_updates: List[Dict[str, Any]],
                                             erp_updates: List[Dict[str, Any]]) -> None:
        """Marca movimientos como conciliados. Solo toca filas que siguen pendientes. Sin commit."""
        if banco_updates:
            self.db.execute(text("""
                UPDATE bancos.movimientos
                   SET estado = :estado, id_acta = :id_acta, regla_cruce = :regla_cruce
                 WHERE id_movimiento = :id_movimiento AND estado IN ('Pendiente', 'NO CONCILIADO')
            """), banco_updates)
        if erp_updates:
            self.db.execute(text("""
                UPDATE bancos.movimientos_erp
                   SET estado = :estado, id_movimiento_banco = :id_movimiento_banco,
                       id_acta = :id_acta, regla_cruce = :regla_cruce, updated_at = now()
                 WHERE id_movimiento_erp = :id_movimiento_erp
                   AND (estado IS NULL OR estado IN ('Pendiente', 'NO CONCILIADO'))
            """), erp_updates)
        self.db.flush()

    # ------------------------------------------------------------------
    # Actas
    # ------------------------------------------------------------------
    def obtener_acta_para_actualizar(self, id_cuenta: UUID, periodo: str) -> Optional[Acta]:
        """Acta de la cuenta y periodo, bloqueada hasta el commit (evita dos ejecuciones simultáneas)."""
        return (
            self.db.query(Acta)
            .filter(Acta.id_cuenta == id_cuenta, Acta.periodo == periodo)
            .with_for_update()
            .first()
        )

    def crear_acta(self, acta: Acta) -> Acta:
        self.db.add(acta)
        self.db.flush()
        return acta

    def listar_actas(self, limit: int = 50, id_cuenta: Optional[UUID] = None) -> List[Acta]:
        q = self.db.query(Acta).options(selectinload(Acta.partidas), selectinload(Acta.cuenta))
        if id_cuenta:
            q = q.filter(Acta.id_cuenta == id_cuenta)
        return q.order_by(Acta.created_at.desc()).limit(limit).all()

    def conteo_transitos(self, actas: List[Acta]) -> Dict[UUID, tuple]:
        """(pendientes banco, pendientes ERP) por acta, en una sola consulta."""
        ids = [a.id_acta for a in actas if a.fecha_inicio and a.fecha_fin]
        if not ids:
            return {}
        filas = self.db.execute(text("""
            SELECT a.id_acta,
                   (SELECT count(*) FROM bancos.movimientos m
                     WHERE m.id_cuenta = a.id_cuenta
                       AND m.fecha_transaccion BETWEEN a.fecha_inicio AND a.fecha_fin
                       AND m.estado IN ('Pendiente', 'NO CONCILIADO')) AS banco,
                   (SELECT count(*) FROM bancos.movimientos_erp e
                     WHERE e.codigo_banco_bc = c.codigo_banco_bc
                       AND e.posting_date BETWEEN a.fecha_inicio AND a.fecha_fin
                       AND (e.estado IS NULL OR e.estado IN ('Pendiente', 'NO CONCILIADO'))) AS erp
              FROM conciliacion.actas a
              JOIN bancos.cuentas_bancarias c ON c.id_cuenta = a.id_cuenta
             WHERE a.id_acta = ANY(:ids)
        """), {"ids": ids}).all()
        return {f.id_acta: (f.banco, f.erp) for f in filas}

    def obtener_acta_por_id(self, id_acta: UUID) -> Optional[Acta]:
        return self.db.query(Acta).options(selectinload(Acta.cuenta)).filter(Acta.id_acta == id_acta).first()

    def reemplazar_partidas(self, acta: Acta, partidas: List[PartidaTransito]) -> None:
        self.db.query(PartidaTransito).filter(PartidaTransito.id_acta == acta.id_acta).delete()
        self.db.add_all(partidas)
        self.db.flush()

    # ------------------------------------------------------------------
    # Grupos conciliados (vista del workbench)
    # ------------------------------------------------------------------
    def obtener_grupos_conciliados(self, id_cuenta: UUID, f_ini: date, f_fin: date) -> List[Dict[str, Any]]:
        res = self.db.execute(text("""
            SELECT b.id_movimiento AS banco_id,
                   DATE(COALESCE(a.updated_at, a.created_at, e.updated_at))::text AS fecha_conciliacion,
                   COALESCE(b.regla_cruce, e.regla_cruce, 'MANUAL') AS regla_aplicada,
                   COALESCE(a.creado_por, 'Sistema') AS creado_por,
                   b.fecha_transaccion::text AS banco_fecha, b.concepto AS banco_concepto,
                   b.numero_referencia AS banco_referencia, b.monto AS banco_monto,
                   COALESCE(b.tipo_movimiento, 'CREDITO') AS banco_tipo,
                   e.id_movimiento_erp AS erp_id, e.document_no AS erp_documento,
                   COALESCE(e.external_document_no, '') AS erp_ext_documento,
                   e.posting_date::text AS erp_fecha, e.description AS erp_concepto,
                   e.monto AS erp_monto, e.codigo_banco_bc AS erp_cuenta
              FROM bancos.movimientos b
              JOIN bancos.movimientos_erp e ON e.id_movimiento_banco = b.id_movimiento
         LEFT JOIN conciliacion.actas a ON b.id_acta = a.id_acta
             WHERE b.id_cuenta = :id_cuenta
               AND b.fecha_transaccion BETWEEN :f_ini AND :f_fin
               AND b.estado LIKE 'CONCILIADO%'
          ORDER BY b.fecha_transaccion DESC, b.id_movimiento
        """), {"id_cuenta": id_cuenta, "f_ini": f_ini, "f_fin": f_fin}).mappings().all()

        grupos: Dict[str, Dict[str, Any]] = {}
        for r in res:
            bid = str(r["banco_id"])
            g = grupos.setdefault(bid, {
                "id_conciliacion": bid,
                "fecha_conciliacion": r["fecha_conciliacion"],
                "regla_aplicada": r["regla_aplicada"],
                "creado_por": r["creado_por"],
                "banco": {
                    "id_movimiento_banco": bid,
                    "fecha": r["banco_fecha"],
                    "concepto": r["banco_concepto"] or "Movimiento Banco",
                    "referencia": r["banco_referencia"] or "S/R",
                    "monto": float(r["banco_monto"] or 0),
                    "tipo": str(r["banco_tipo"]).upper(),
                },
                "partidas_bc": [],
            })
            g["partidas_bc"].append({
                "id_movimiento_bc": str(r["erp_id"]),
                "num_documento": r["erp_documento"] or "DOC-BC",
                "external_document_no": r["erp_ext_documento"],
                "fecha": r["erp_fecha"],
                "concepto": r["erp_concepto"] or "Asiento Contable",
                "monto": float(r["erp_monto"] or 0),
                "cuenta_contable": r["erp_cuenta"] or "",
            })
        return list(grupos.values())
