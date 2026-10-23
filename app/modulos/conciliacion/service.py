# app/modulos/conciliacion/service.py
import logging
import uuid
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modulos.bancos.models import CuentaBancaria
from app.modulos.conciliacion import diagnostico, motor
from app.modulos.conciliacion.bc_service import BusinessCentralReaderService
from app.modulos.conciliacion.models import Acta, PartidaTransito
from app.modulos.conciliacion.repository import ConciliacionRepository

logger = logging.getLogger(__name__)

EMPRESA = "FÁBRICA DE BATERÍAS FABRIBAT CIA. LTDA."
ESTADOS_EDITABLES = ("BORRADOR",)
MESES = ("Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
         "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre")
CERO = Decimal("0.00")


def rango_de_periodo(periodo: str) -> Tuple[date, date]:
    """'2026-08' -> (2026-08-01, 2026-08-31)."""
    try:
        anio, mes = (int(p) for p in periodo.split("-")[:2])
        return date(anio, mes, 1), date(anio, mes, monthrange(anio, mes)[1])
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "El periodo debe tener el formato AAAA-MM.")


def nombre_periodo(f_ini: date, f_fin: date) -> str:
    if f_ini.day == 1 and f_fin == rango_de_periodo(f"{f_ini.year}-{f_ini.month}")[1]:
        return f"{MESES[f_ini.month - 1]} {f_ini.year}"
    return f"{f_ini:%d/%m/%Y} al {f_fin:%d/%m/%Y}"


def _dec(v: Any) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))


def _mov_banco(m) -> motor.MovBanco:
    return motor.MovBanco(m.id_movimiento, m.fecha_transaccion, _dec(m.monto),
                          motor.referencias_banco(m.numero_referencia, m.info_adicional),
                          m.numero_referencia or "",
                          motor.referencias_adicionales(m.info_adicional))


def _mov_erp(e) -> motor.MovErp:
    return motor.MovErp(e.id_movimiento_erp, e.posting_date, _dec(e.monto),
                        motor.referencias_de_texto(e.document_no, e.external_document_no),
                        e.document_no or "", motor.normalizar_referencia(e.external_document_no))


class ConciliacionService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = ConciliacionRepository(db)

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------
    def _cuenta(self, id_cuenta: UUID) -> CuentaBancaria:
        cuenta = self.repo.obtener_cuenta_bancaria(id_cuenta)
        if not cuenta:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Cuenta bancaria no encontrada.")
        if not cuenta.codigo_banco_bc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "La cuenta no tiene código de banco de Business Central.")
        return cuenta

    def _sincronizar_bc(self, cuenta: CuentaBancaria, f_ini: date, f_fin: date) -> Tuple[Dict[str, Any], Optional[str]]:
        entradas, error = BusinessCentralReaderService.consultar_mayor_bancos(f_ini, f_fin, cuenta.codigo_banco_bc)
        if error:
            return {"nuevos": 0, "modificados": 0, "total_sincronizados": 0, "requiere_rematch": False}, error
        return self.repo.upsert_movimientos_erp_espejo(entradas), None

    # ------------------------------------------------------------------
    # Mayor (libro de bancos de BC)
    # ------------------------------------------------------------------
    def obtener_y_preparar_mayor(self, f_ini: date, f_fin: date, cuenta_contable: Optional[str] = None,
                                 id_cuenta: Optional[UUID] = None) -> Dict[str, Any]:
        cuenta = self._cuenta(id_cuenta) if id_cuenta else self.repo.resolver_cuenta(cuenta_contable or "")
        if not cuenta:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                "No se encontró una única cuenta bancaria para esa cuenta contable.")

        novedades, error = self._sincronizar_bc(cuenta, f_ini, f_fin)
        self.db.commit()
        pendientes = self.repo.movimientos_erp_pendientes(cuenta.codigo_banco_bc, f_ini, f_fin)
        return {
            "status": "success" if not error else "warning",
            "sincronizado": error is None,
            "advertencia": (f"{error} Se muestran los últimos datos guardados." if error else None),
            "total_registros": len(pendientes),
            "novedades_sync": novedades,
            "data": [
                {
                    "id_movimiento_erp": str(e.id_movimiento_erp),
                    "entry_no_bc": e.entry_no_bc,
                    "posting_date": e.posting_date.isoformat(),
                    "document_no": e.document_no,
                    "external_document_no": e.external_document_no or "",
                    "description": e.description,
                    "monto": float(e.monto),
                    "estado": e.estado or "NO CONCILIADO",
                    "regla_cruce": e.regla_cruce,
                }
                for e in pendientes
            ],
        }

    # ------------------------------------------------------------------
    # Ejecución del cruce
    # ------------------------------------------------------------------
    def ejecutar_conciliacion_periodo(self, id_cuenta: UUID, periodo: str, f_ini: date, f_fin: date,
                                      creado_por: str) -> Dict[str, Any]:
        if f_ini > f_fin:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "La fecha inicial es posterior a la final.")
        cuenta = self._cuenta(id_cuenta)

        try:
            # 1. Acta del periodo (bloqueada para evitar ejecuciones simultáneas)
            acta = self.repo.obtener_acta_para_actualizar(cuenta.id_cuenta, periodo)
            if acta and acta.estado not in ESTADOS_EDITABLES:
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    f"El acta de {periodo} está {acta.estado}; no se puede volver a conciliar.")
            if not acta:
                acta = self.repo.crear_acta(Acta(
                    id_acta=uuid.uuid4(), id_cuenta=cuenta.id_cuenta,
                    cuenta_contable=cuenta.cuenta_contable_bc or cuenta.codigo_banco_bc,
                    periodo=periodo, fecha_inicio=f_ini, fecha_fin=f_fin,
                    saldo_banco=CERO, saldo_libros=CERO, total_conciliado=CERO,
                    creado_por=creado_por, estado="BORRADOR",
                ))

            # 2. Espejo de BC actualizado (si BC falla no se concilia con datos viejos)
            _, error = self._sincronizar_bc(cuenta, f_ini, f_fin)
            if error:
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, error)

            # 3. Cruce en memoria
            pend_banco = self.repo.movimientos_banco_pendientes(cuenta.id_cuenta, f_ini, f_fin)
            pend_erp = self.repo.movimientos_erp_pendientes(cuenta.codigo_banco_bc, f_ini, f_fin)
            resultado = motor.ejecutar_motor(
                [_mov_banco(m) for m in pend_banco if m.monto],
                [_mov_erp(e) for e in pend_erp if e.monto],
                es_banco_guayaquil="GUAYAQUIL" in f"{cuenta.banco_nombre} {cuenta.grupo_registro_bc}".upper(),
            )

            # 4. Marcas de conciliación
            banco_updates, erp_updates = [], []
            for c in resultado.cruces:
                banco_updates.append({"id_movimiento": c.id_banco, "estado": c.estado,
                                      "id_acta": acta.id_acta, "regla_cruce": c.regla})
                erp_updates.extend({"id_movimiento_erp": i, "id_movimiento_banco": c.id_banco,
                                    "estado": c.estado, "id_acta": acta.id_acta, "regla_cruce": c.regla}
                                   for i in c.ids_erp)
            # Reversos dentro de BC: conciliados entre sí, sin movimiento bancario
            for par in resultado.reversos:
                erp_updates.extend({"id_movimiento_erp": i, "id_movimiento_banco": None,
                                    "estado": motor.ESTADO_EXACTO, "id_acta": acta.id_acta,
                                    "regla_cruce": motor.REGLA_REVERSO} for i in par)
            self.repo.aplicar_actualizaciones_conciliacion(banco_updates, erp_updates)

            # 5. Totales del periodo completo (incluye cruces de ejecuciones anteriores)
            tot = self.repo.totales_periodo(cuenta.id_cuenta, cuenta.codigo_banco_bc, f_ini, f_fin)
            acta.fecha_inicio, acta.fecha_fin = f_ini, f_fin
            acta.saldo_banco = tot["creditos_banco"] + tot["debitos_banco"]   # movimiento neto del extracto
            acta.saldo_libros = tot["neto_libros"]                            # movimiento neto en BC
            acta.total_conciliado = tot["conciliado_banco"]
            acta.creado_por = creado_por
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "Ya hay una conciliación en curso para esta cuenta y periodo. Intente de nuevo.")
        except Exception:
            self.db.rollback()
            raise

        transitos_banco = len(pend_banco) - len(resultado.ids_banco)
        transitos_erp = len(pend_erp) - len(resultado.ids_erp)
        logger.info("Conciliación %s %s por %s: %d cruces, %d reversos en BC", cuenta.codigo_banco_bc, periodo,
                    creado_por, len(resultado.cruces), len(resultado.reversos))
        return {
            "id_acta": str(acta.id_acta),
            "periodo": periodo,
            "cuenta_contable": acta.cuenta_contable,
            "saldo_banco": float(acta.saldo_banco),
            "saldo_libros": float(acta.saldo_libros),
            "total_conciliado": float(acta.total_conciliado),
            "movimientos_conciliados": len(resultado.cruces),
            "reversos_bc": len(resultado.reversos),
            "transitos_banco": transitos_banco,
            "transitos_erp": transitos_erp,
            "total_partidas_en_transito": transitos_banco + transitos_erp,
            "estado": acta.estado,
        }

    # ------------------------------------------------------------------
    # Detalle / reporte del acta
    # ------------------------------------------------------------------
    def _acta(self, id_acta: UUID) -> Acta:
        acta = self.repo.obtener_acta_por_id(id_acta)
        if not acta:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "El acta especificada no existe.")
        return acta

    def _fechas_acta(self, acta: Acta) -> Tuple[date, date]:
        if acta.fecha_inicio and acta.fecha_fin:
            return acta.fecha_inicio, acta.fecha_fin
        return rango_de_periodo(acta.periodo)

    def _partidas_pendientes(self, acta: Acta) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Partidas en tránsito: snapshot guardado si el acta está cerrada; si no, pendientes actuales."""
        if acta.estado not in ESTADOS_EDITABLES and acta.partidas:
            banco = [p for p in acta.partidas if p.origen_dato == "BANCO"]
            erp = [p for p in acta.partidas if p.origen_dato == "ERP"]
            fmt = lambda p: {"id": str(p.id_partida), "fecha": p.fecha_transaccion.isoformat(),
                             "referencia": p.documento_referencia or "", "concepto": p.descripcion or "",
                             "monto": float(p.monto), "tipo": "CREDITO" if p.monto >= 0 else "DEBITO",
                             "estado": "PENDIENTE"}
            return [fmt(p) for p in banco], [fmt(p) for p in erp]

        f_ini, f_fin = self._fechas_acta(acta)
        cuenta = acta.cuenta
        banco = self.repo.movimientos_banco_pendientes(acta.id_cuenta, f_ini, f_fin)
        erp = self.repo.movimientos_erp_pendientes(cuenta.codigo_banco_bc, f_ini, f_fin) if cuenta else []
        return (
            [{"id": str(m.id_movimiento), "fecha": m.fecha_transaccion.isoformat(),
              "referencia": m.numero_referencia or "", "concepto": m.concepto or "",
              "monto": float(m.monto), "tipo": "CREDITO" if m.monto >= 0 else "DEBITO",
              "regla_cruce": m.regla_cruce, "estado": m.estado} for m in banco],
            [{"id": str(e.id_movimiento_erp), "fecha": e.posting_date.isoformat(),
              "referencia": e.external_document_no or e.document_no or "", "documento": e.document_no or "",
              "entry_no": e.entry_no_bc, "concepto": e.description or "",
              "monto": float(e.monto), "tipo": "CREDITO" if e.monto >= 0 else "DEBITO",
              "regla_cruce": e.regla_cruce, "estado": e.estado or "NO CONCILIADO"} for e in erp],
        )

    def listar_actas(self, limit: int = 50, id_cuenta: Optional[UUID] = None) -> List[Dict[str, Any]]:
        actas = self.repo.listar_actas(limit, id_cuenta)
        conteos = self.repo.conteo_transitos([a for a in actas if a.estado in ESTADOS_EDITABLES])
        salida = []
        for a in actas:
            if a.estado in ESTADOS_EDITABLES:
                nb, ne = conteos.get(a.id_acta, (0, 0))
            else:
                nb = sum(1 for p in a.partidas if p.origen_dato == "BANCO")
                ne = sum(1 for p in a.partidas if p.origen_dato == "ERP")
            salida.append(self._acta_dict(a, nb, ne))
        return salida

    def obtener_detalle_acta(self, id_acta: UUID) -> Dict[str, Any]:
        acta = self._acta(id_acta)
        banco, erp = self._partidas_pendientes(acta)
        return {"acta": self._acta_dict(acta, len(banco), len(erp)),
                "partidas_transito_banco": banco, "partidas_transito_erp": erp}

    @staticmethod
    def _acta_dict(acta: Acta, n_banco: int = 0, n_erp: int = 0) -> Dict[str, Any]:
        return {
            "id_acta": str(acta.id_acta),
            "id_cuenta": str(acta.id_cuenta) if acta.id_cuenta else None,
            "periodo": acta.periodo,
            "fecha_inicio": acta.fecha_inicio.isoformat() if acta.fecha_inicio else None,
            "fecha_fin": acta.fecha_fin.isoformat() if acta.fecha_fin else None,
            "cuenta_contable": acta.cuenta_contable,
            "banco": acta.cuenta.banco_nombre if acta.cuenta else None,
            "saldo_banco": float(acta.saldo_banco or 0),
            "saldo_libros": float(acta.saldo_libros or 0),
            "total_conciliado": float(acta.total_conciliado or 0),
            "transitos_banco": n_banco,
            "transitos_erp": n_erp,
            "total_partidas_en_transito": n_banco + n_erp,
            "estado": acta.estado,
            "creado_por": acta.creado_por,
            "created_at": acta.created_at.isoformat() if acta.created_at else None,
            "updated_at": acta.updated_at.isoformat() if acta.updated_at else None,
        }

    def obtener_datos_reporte_acta(self, id_acta: UUID, generado_por: str = "") -> Dict[str, Any]:
        """
        Datos del informe. Concilia el MOVIMIENTO del periodo:
            neto extracto - neto libros = partidas del banco sin registrar - partidas de libros sin reflejar
        Si cuadra, la "diferencia no explicada" es 0.
        """
        acta = self._acta(id_acta)
        f_ini, f_fin = self._fechas_acta(acta)
        cuenta = acta.cuenta
        tot = self.repo.totales_periodo(acta.id_cuenta, cuenta.codigo_banco_bc, f_ini, f_fin)
        saldos = self.repo.saldos_extracto(acta.id_cuenta, f_ini, f_fin)
        banco, erp = self._partidas_pendientes(acta)

        def items(lista, condicion):
            return [{"fecha": date.fromisoformat(p["fecha"]).strftime("%d/%m/%Y"),
                     "numero": p["referencia"], "concepto": p["concepto"],
                     "monto": _dec(p["monto"])} for p in lista if condicion(p["monto"])]

        neto_banco = tot["creditos_banco"] + tot["debitos_banco"]
        neto_libros = tot["neto_libros"]
        pend_banco = sum((_dec(p["monto"]) for p in banco), CERO)
        pend_erp = sum((_dec(p["monto"]) for p in erp), CERO)
        diferencia = neto_banco - neto_libros
        saldo_ini = saldos["saldo_inicial"]

        return {
            "empresa": EMPRESA,
            "periodo_nombre": nombre_periodo(f_ini, f_fin),
            "banco": cuenta.banco_nombre if cuenta else "Banco",
            "numero_cuenta": cuenta.numero_cuenta if cuenta else "",
            "cuenta_contable": acta.cuenta_contable,
            "fecha_inicio": f_ini.strftime("%d/%m/%Y"),
            "fecha_fin": f_fin.strftime("%d/%m/%Y"),
            "estado": acta.estado,
            "saldo_extracto_inicial": saldo_ini,
            "total_creditos": tot["creditos_banco"],
            "total_debitos": tot["debitos_banco"],
            "saldo_extracto_calculado": (saldo_ini + neto_banco) if saldo_ini is not None else None,
            "saldo_extracto_reportado": saldos["saldo_final"],
            "fecha_saldo_inicial": saldos["fecha_saldo_inicial"].strftime("%d/%m/%Y") if saldos["fecha_saldo_inicial"] else None,
            "fecha_saldo_final": saldos["fecha_saldo_final"].strftime("%d/%m/%Y") if saldos["fecha_saldo_final"] else None,
            "diferencia_extracto": (saldos["saldo_final"] - (saldo_ini + neto_banco))
            if (saldo_ini is not None and saldos["saldo_final"] is not None) else None,
            "neto_banco": neto_banco,
            "neto_libros": neto_libros,
            "total_conciliado": tot["conciliado_banco"],
            "diferencia": diferencia,
            "diferencia_no_explicada": diferencia - (pend_banco - pend_erp),
            "notas_credito": items(banco, lambda m: m > 0),     # el banco acreditó y BC no lo tiene
            "notas_debito": items(banco, lambda m: m < 0),      # el banco debitó y BC no lo tiene
            "depositos_transito": items(erp, lambda m: m > 0),  # BC registró ingreso que el banco no muestra
            "pagos_transito": items(erp, lambda m: m < 0),      # BC registró egreso que el banco no muestra
            # Informativo: asientos de BC reversados (se anulan entre sí, no afectan la diferencia)
            "reversos_libros": [{"fecha": r["posting_date"].strftime("%d/%m/%Y"), "numero": r["document_no"],
                                 "concepto": r["description"] or "", "monto": _dec(r["monto"])}
                                for r in self.repo.listar_reversos_bc(cuenta.codigo_banco_bc, f_ini, f_fin)],
            "generado_por": generado_por,
            "generado_el": datetime.now(ZoneInfo("America/Guayaquil")).strftime("%d/%m/%Y %H:%M"),
        }

    # ------------------------------------------------------------------
    # Reporte "Detalle de cruces" (validación de reglas)
    # ------------------------------------------------------------------
    def obtener_datos_reporte_cruces(self, id_cuenta: UUID, f_ini: date, f_fin: date,
                                     generado_por: str = "") -> Dict[str, Any]:
        """
        Qué movimiento del banco cruzó con qué asiento de BC (y con qué regla), los reversos
        de BC y los pendientes de ambos lados con la observación de por qué no cruzaron.
        Solo lectura: no sincroniza con BC ni modifica cruces.
        """
        if f_ini > f_fin:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "La fecha inicial es posterior a la final.")
        cuenta = self._cuenta(id_cuenta)
        codigo = cuenta.codigo_banco_bc

        banco_conc = self.repo.movimientos_banco_conciliados(cuenta.id_cuenta, f_ini, f_fin)
        erp_cruzados = self.repo.movimientos_erp_de_banco([m.id_movimiento for m in banco_conc])
        erp_por_banco: Dict[Any, List[Any]] = {}
        for e in erp_cruzados:
            erp_por_banco.setdefault(e.id_movimiento_banco, []).append(e)

        # --- Cruzados: una fila por pareja banco <-> asiento de BC ---
        cruzados: List[Dict[str, Any]] = []
        grupo = 0
        for m in banco_conc:
            asientos = erp_por_banco.get(m.id_movimiento, [])
            grupo += 1
            mb = _mov_banco(m)
            regla = m.regla_cruce or (asientos[0].regla_cruce if asientos else None)
            suma_bc = sum((_dec(e.monto) for e in asientos), CERO)
            base = {
                "grupo": grupo, "regla": regla or "MANUAL", "regla_nombre": diagnostico.nombre_regla(regla),
                "estado": m.estado, "banco_fecha": m.fecha_transaccion, "banco_referencia": m.numero_referencia or "",
                "banco_concepto": m.concepto or "", "banco_info": m.info_adicional or "",
                "banco_monto": _dec(m.monto), "suma_bc": suma_bc, "cuadra": suma_bc == _dec(m.monto),
                "n_asientos": len(asientos),
            }
            if not asientos:
                cruzados.append({**base, "bc_entry": None, "bc_fecha": None, "bc_documento": "",
                                 "bc_externo": "", "bc_descripcion": "(sin asiento de BC vinculado)",
                                 "bc_monto": None, "dias": None, "referencia": ""})
                continue
            for e in asientos:
                me = _mov_erp(e)
                cruzados.append({**base, "bc_entry": e.entry_no_bc, "bc_fecha": e.posting_date,
                                 "bc_documento": e.document_no or "", "bc_externo": e.external_document_no or "",
                                 "bc_descripcion": e.description or "", "bc_monto": _dec(e.monto),
                                 "dias": (e.posting_date - m.fecha_transaccion).days,
                                 "referencia": diagnostico.referencia_coincidente(mb, me, regla)})

        # --- Reversos dentro de BC ---
        reversos = [{"documento": r["document_no"] or "", "fecha": r["posting_date"], "entry": r["entry_no_bc"],
                     "externo": r["external_document_no"] or "", "descripcion": r["description"] or "",
                     "monto": _dec(r["monto"])}
                    for r in self.repo.listar_reversos_bc(codigo, f_ini, f_fin)]

        # --- Pendientes con observación ---
        pend_banco = self.repo.movimientos_banco_pendientes(cuenta.id_cuenta, f_ini, f_fin)
        pend_erp = self.repo.movimientos_erp_pendientes(codigo, f_ini, f_fin)
        erp_conc = [e for e in self.repo.movimientos_erp_conciliados(codigo, f_ini, f_fin)
                    if e.regla_cruce != motor.REGLA_REVERSO]
        obs_banco, obs_erp = diagnostico.diagnosticar_pendientes(
            [_mov_banco(m) for m in pend_banco], [_mov_erp(e) for e in pend_erp],
            [_mov_banco(m) for m in banco_conc], [_mov_erp(e) for e in erp_conc])
        pendientes_banco = [{"fecha": m.fecha_transaccion, "referencia": m.numero_referencia or "",
                             "concepto": m.concepto or "", "info": m.info_adicional or "",
                             "tipo": "CRÉDITO" if m.monto >= 0 else "DÉBITO", "monto": _dec(m.monto),
                             "observacion": obs_banco.get(m.id_movimiento, "")} for m in pend_banco]
        pendientes_erp = [{"entry": e.entry_no_bc, "fecha": e.posting_date, "documento": e.document_no or "",
                           "externo": e.external_document_no or "", "descripcion": e.description or "",
                           "tipo": "INGRESO" if e.monto >= 0 else "EGRESO", "monto": _dec(e.monto),
                           "observacion": obs_erp.get(e.id_movimiento_erp, "")} for e in pend_erp]

        # --- Resumen por regla ---
        por_regla: Dict[str, Dict[str, Any]] = {}
        vistos = set()
        for f in cruzados:
            r = por_regla.setdefault(f["regla"], {"regla": f["regla"], "nombre": f["regla_nombre"],
                                                  "descripcion": diagnostico.NOMBRES_REGLA.get(f["regla"], ("", ""))[1],
                                                  "grupos": 0, "banco": 0, "asientos": 0, "monto": CERO,
                                                  "parcial": f["estado"] == motor.ESTADO_PARCIAL})
            if f["grupo"] not in vistos:
                vistos.add(f["grupo"])
                r["grupos"] += 1
                r["banco"] += 1
                r["monto"] += f["banco_monto"]
            if f["bc_monto"] is not None:
                r["asientos"] += 1
        if reversos:
            por_regla[motor.REGLA_REVERSO] = {
                "regla": motor.REGLA_REVERSO, "nombre": diagnostico.nombre_regla(motor.REGLA_REVERSO),
                "descripcion": diagnostico.NOMBRES_REGLA[motor.REGLA_REVERSO][1],
                "grupos": len(reversos) // 2, "banco": 0, "asientos": len(reversos), "monto": CERO, "parcial": False}
        orden = [motor.REGLA_REVERSO, motor.REGLA_1, motor.REGLA_1_GYE, motor.REGLA_1N, motor.REGLA_2,
                 motor.REGLA_3, motor.REGLA_REF_ADICIONAL]
        resumen_reglas = sorted(por_regla.values(),
                                key=lambda r: orden.index(r["regla"]) if r["regla"] in orden else len(orden))

        conteo = self.repo.contar_movimientos_periodo(cuenta.id_cuenta, codigo, f_ini, f_fin)
        acta = self.repo.obtener_acta(cuenta.id_cuenta, f"{f_ini:%Y-%m}")
        return {
            "empresa": EMPRESA,
            "banco": cuenta.banco_nombre or "Banco",
            "numero_cuenta": cuenta.numero_cuenta or "",
            "codigo_bc": codigo or "",
            "periodo_nombre": nombre_periodo(f_ini, f_fin),
            "fecha_inicio": f_ini, "fecha_fin": f_fin,
            "estado_acta": acta.estado if acta else "Sin acta",
            "resumen_reglas": resumen_reglas,
            "cruzados": cruzados,
            "reversos": reversos,
            "pendientes_banco": pendientes_banco,
            "pendientes_erp": pendientes_erp,
            "control": {
                "banco_total": conteo["banco"], "banco_conciliados": len(banco_conc), "banco_pendientes": len(pend_banco),
                "erp_total": conteo["erp"], "erp_cruzados": len(erp_cruzados), "erp_reversos": len(reversos),
                "erp_pendientes": len(pend_erp),
                "erp_cruzados_fuera_periodo": sum(1 for e in erp_cruzados
                                                  if not (f_ini <= e.posting_date <= f_fin)),
                "monto_pend_banco": sum((p["monto"] for p in pendientes_banco), CERO),
                "monto_pend_erp": sum((p["monto"] for p in pendientes_erp), CERO),
            },
            "parametros": [
                ("Ventana de fechas (R1, R1N, R2, R3)", f"±{motor.VENTANA_DIAS} días"),
                ("Ventana de fechas (R5, referencia adicional)", f"±{motor.VENTANA_REF_ADICIONAL} días"),
                ("Máximo de asientos en una combinación (R3)", str(motor.MAX_TAMANO_COMBINACION)),
                ("Candidatos evaluados para tríos (R3)", str(motor.MAX_CANDIDATOS_COMBINATORIA)),
                ("Monto", "Exacto al centavo, mismo signo"),
            ],
            "generado_por": generado_por,
            "generado_el": datetime.now(ZoneInfo("America/Guayaquil")).strftime("%d/%m/%Y %H:%M"),
        }

    # ------------------------------------------------------------------
    # Ciclo de vida del acta
    # ------------------------------------------------------------------
    def cerrar_acta(self, id_acta: UUID, usuario: str) -> Dict[str, Any]:
        """Congela las partidas en tránsito del acta y la pasa a CERRADO."""
        acta = self._acta(id_acta)
        if acta.estado != "BORRADOR":
            raise HTTPException(status.HTTP_409_CONFLICT, f"El acta ya está {acta.estado}.")
        banco, erp = self._partidas_pendientes(acta)
        partidas = [
            PartidaTransito(id_acta=acta.id_acta, origen_dato=origen,
                            fecha_transaccion=date.fromisoformat(p["fecha"]),
                            documento_referencia=(p["referencia"] or "")[:100],
                            descripcion=(p["concepto"] or "")[:255], monto=_dec(p["monto"]))
            for origen, lista in (("BANCO", banco), ("ERP", erp)) for p in lista
        ]
        self.repo.reemplazar_partidas(acta, partidas)
        acta.estado = "CERRADO"
        self.db.commit()
        logger.info("Acta %s cerrada por %s", acta.id_acta, usuario)
        return self._acta_dict(acta, len(banco), len(erp))

    def reabrir_acta(self, id_acta: UUID, usuario: str) -> Dict[str, Any]:
        acta = self._acta(id_acta)
        if acta.estado == "BORRADOR":
            raise HTTPException(status.HTTP_409_CONFLICT, "El acta ya está en BORRADOR.")
        acta.estado = "BORRADOR"
        self.db.commit()
        logger.info("Acta %s reabierta por %s", acta.id_acta, usuario)
        return self._acta_dict(acta)
