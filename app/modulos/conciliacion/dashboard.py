# app/modulos/conciliacion/dashboard.py
"""
Dashboard de conciliación: indicadores por cuenta y consolidados para un rango de meses.

Solo lectura y solo con datos ya guardados (no sincroniza con BC ni ejecuta el motor).

Cuadre por saldos (al último día del rango), cuando la cuenta tiene corte inicial:
    saldo banco  = saldo del banco en el corte + movimientos del extracto posteriores al corte
    saldo libros = saldo de libros en el corte + movimientos de BC (espejo) posteriores al corte
    tránsito     = partidas iniciales pendientes + lo que, a esa fecha, no tiene contrapartida
                   (pendiente, o cruzado con algo fechado después del fin del rango)
    saldo libros calculado = saldo banco − tránsito del banco + tránsito de BC (± partidas iniciales)
Sin corte inicial se muestra el cuadre del movimiento del rango (como el acta).
"""
from __future__ import annotations

from calendar import monthrange
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modulos.bancos.models import CuentaBancaria
from app.modulos.conciliacion import diagnostico, motor
from app.modulos.conciliacion.corte_inicial import SIGNO
from app.modulos.conciliacion.models import CorteInicial

CERO = Decimal("0.00")
PEND = ("Pendiente", "NO CONCILIADO")
MESES = ("Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")

# Líneas del puente del saldo del banco al de libros (mismo orden que el acta en Excel)
LINEAS = [
    ("CHEQUE_NO_COBRADO", "Cheques girados y no cobrados", -1),
    ("NOTA_DEBITO_NO_REGISTRADA", "Notas de débito no registradas", 1),
    ("NOTA_CREDITO_NO_REGISTRADA", "Notas de crédito no registradas", -1),
    ("PENDIENTE_ACREDITAR", "Valores pendientes por acreditar", 1),
    ("PENDIENTE_DEBITAR", "Valores pendientes por debitar", -1),
]


def _d(v: Any) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))


def _f(v: Any) -> float:
    return float(_d(v))


def rango_meses(desde: str, hasta: str) -> tuple[date, date]:
    try:
        a1, m1 = (int(x) for x in desde.split("-")[:2])
        a2, m2 = (int(x) for x in hasta.split("-")[:2])
        f_ini, f_fin = date(a1, m1, 1), date(a2, m2, monthrange(a2, m2)[1])
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Los periodos deben tener el formato AAAA-MM.")
    if f_ini > f_fin:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "El periodo inicial es posterior al final.")
    if (f_fin.year - f_ini.year) * 12 + f_fin.month - f_ini.month > 23:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "El rango máximo es de 24 meses.")
    return f_ini, f_fin


def _causa(obs: str) -> str:
    """Agrupa la observación del diagnóstico en una causa corta."""
    o = obs.lower()
    if o.startswith("ambiguo"):
        return "Ambiguo"
    if "ya conciliado" in o:
        return "Ya conciliado con otro"
    if "signo contrario" in o:
        return "Signo contrario"
    if "fuera de la ventana" in o or "ventana ±" in o:
        return "Fuera de ventana"
    if "otro monto" in o or "no igualan" in o:
        return "Otro monto"
    if o.startswith("candidato"):
        return "Falta ejecutar el cruce"
    return "Sin contrapartida"


class DashboardService:
    def __init__(self, db: Session):
        self.db = db

    def _q(self, sql: str, **params):
        return self.db.execute(text(sql), params)

    # ------------------------------------------------------------------
    def _cuentas(self, banco: Optional[str], id_cuenta: Optional[UUID]) -> List[CuentaBancaria]:
        cuentas = self.db.query(CuentaBancaria).filter(CuentaBancaria.activo.is_(True)).order_by(
            CuentaBancaria.nombre_cuenta).all()
        if banco:
            cuentas = [c for c in cuentas if (c.banco_nombre or "").upper() == banco.upper()]
        if id_cuenta:
            cuentas = [c for c in cuentas if c.id_cuenta == id_cuenta]
        return cuentas

    def _pendientes_al_corte(self, c: CuentaBancaria, desde: date, hasta: date) -> tuple[list, list]:
        """
        Movimientos sin contrapartida a la fecha `hasta`: pendientes, o cruzados con algo
        fechado después de `hasta` (el grupo completo se considera en tránsito).
        """
        banco = [dict(r) for r in self._q("""
            SELECT b.id_movimiento AS id, b.fecha_transaccion AS fecha, b.numero_referencia AS ref,
                   b.concepto AS detalle, b.monto
              FROM bancos.movimientos b
             WHERE b.id_cuenta = :c AND b.fecha_transaccion BETWEEN :d AND :h
               AND (b.estado IN ('Pendiente', 'NO CONCILIADO')
                    OR EXISTS (SELECT 1 FROM bancos.movimientos_erp e
                                WHERE e.id_movimiento_banco = b.id_movimiento AND e.posting_date > :h))
        """, c=c.id_cuenta, d=desde, h=hasta).mappings()]
        erp = [dict(r) for r in self._q("""
            SELECT e.id_movimiento_erp AS id, e.posting_date AS fecha,
                   COALESCE(NULLIF(e.external_document_no, ''), e.document_no) AS ref,
                   e.description AS detalle, e.monto
              FROM bancos.movimientos_erp e
             WHERE e.codigo_banco_bc = :cod AND e.posting_date BETWEEN :d AND :h
               AND COALESCE(e.regla_cruce, '') <> 'REGLA_REVERSO_BC'
               AND (e.estado IS NULL OR e.estado IN ('Pendiente', 'NO CONCILIADO')
                    OR EXISTS (SELECT 1 FROM bancos.movimientos b WHERE b.id_movimiento = e.id_movimiento_banco
                                AND b.fecha_transaccion > :h)
                    OR EXISTS (SELECT 1 FROM bancos.movimientos_erp o WHERE o.id_movimiento_banco = e.id_movimiento_banco
                                AND o.posting_date > :h))
        """, cod=c.codigo_banco_bc, d=desde, h=hasta).mappings()]
        return banco, erp

    # ------------------------------------------------------------------
    def _cuenta(self, c: CuentaBancaria, f_ini: date, f_fin: date, hoy: date) -> Dict[str, Any]:
        b = self._q("""
            SELECT count(*) AS n, COALESCE(sum(abs(monto)), 0) AS bruto,
                   count(*) FILTER (WHERE estado LIKE 'CONCILIADO%') AS n_conc,
                   COALESCE(sum(abs(monto)) FILTER (WHERE estado LIKE 'CONCILIADO%'), 0) AS bruto_conc,
                   count(*) FILTER (WHERE estado = 'CONCILIADO_PARCIAL') AS n_parcial,
                   count(*) FILTER (WHERE estado IN ('Pendiente', 'NO CONCILIADO')) AS n_pend,
                   COALESCE(sum(monto) FILTER (WHERE estado IN ('Pendiente', 'NO CONCILIADO')), 0) AS pend,
                   COALESCE(sum(monto), 0) AS neto,
                   COALESCE(sum(monto) FILTER (WHERE monto > 0 AND COALESCE(regla_cruce, '') <> 'REGLA_REVERSO_BANCO'), 0) AS ing,
                   COALESCE(sum(monto) FILTER (WHERE monto < 0 AND COALESCE(regla_cruce, '') <> 'REGLA_REVERSO_BANCO'), 0) AS egr,
                   count(*) FILTER (WHERE monto > 0) AS n_ing, count(*) FILTER (WHERE monto < 0) AS n_egr
              FROM bancos.movimientos WHERE id_cuenta = :c AND fecha_transaccion BETWEEN :d AND :h
        """, c=c.id_cuenta, d=f_ini, h=f_fin).mappings().one()
        e = self._q("""
            SELECT count(*) AS n,
                   count(*) FILTER (WHERE estado IS NULL OR estado IN ('Pendiente', 'NO CONCILIADO')) AS n_pend,
                   COALESCE(sum(monto) FILTER (WHERE estado IS NULL OR estado IN ('Pendiente', 'NO CONCILIADO')), 0) AS pend,
                   COALESCE(sum(monto), 0) AS neto,
                   COALESCE(sum(monto) FILTER (WHERE monto > 0 AND COALESCE(regla_cruce, '') <> 'REGLA_REVERSO_BC'), 0) AS ing,
                   COALESCE(sum(monto) FILTER (WHERE monto < 0 AND COALESCE(regla_cruce, '') <> 'REGLA_REVERSO_BC'), 0) AS egr,
                   count(*) FILTER (WHERE monto > 0) AS n_ing, count(*) FILTER (WHERE monto < 0) AS n_egr
              FROM bancos.movimientos_erp WHERE codigo_banco_bc = :cod AND posting_date BETWEEN :d AND :h
        """, cod=c.codigo_banco_bc, d=f_ini, h=f_fin).mappings().one()

        extracto_hasta = self._q("""
            SELECT GREATEST(
                (SELECT max(fecha_hasta) FROM bancos.cargas_extractos WHERE id_cuenta = :c AND estado = 'Completado'),
                (SELECT max(fecha_transaccion) FROM bancos.movimientos WHERE id_cuenta = :c))
        """, c=c.id_cuenta).scalar()
        acta = self._q("SELECT estado FROM conciliacion.actas WHERE id_cuenta = :c AND periodo = :p",
                       c=c.id_cuenta, p=f"{f_fin:%Y-%m}").scalar()
        anteriores_abiertas = self._q("""
            SELECT count(*) FROM conciliacion.actas
             WHERE id_cuenta = :c AND estado = 'BORRADOR' AND periodo < :p
        """, c=c.id_cuenta, p=f"{hoy:%Y-%m}").scalar() or 0

        # Cuadre del movimiento del rango (mismo cálculo que el acta)
        dif_mov = _d(b["neto"]) - _d(e["neto"]) - (_d(b["pend"]) - _d(e["pend"]))

        corte = self.db.query(CorteInicial).filter(CorteInicial.id_cuenta == c.id_cuenta).first()
        cuadre = None
        iniciales = []
        if corte and f_fin > corte.fecha_corte:
            desde_corte = corte.fecha_corte + timedelta(days=1)
            netos = self._q("""
                SELECT (SELECT COALESCE(sum(monto), 0) FROM bancos.movimientos
                         WHERE id_cuenta = :c AND fecha_transaccion BETWEEN :d AND :h) AS banco,
                       (SELECT COALESCE(sum(monto), 0) FROM bancos.movimientos_erp
                         WHERE codigo_banco_bc = :cod AND posting_date BETWEEN :d AND :h) AS libros
            """, c=c.id_cuenta, cod=c.codigo_banco_bc, d=desde_corte, h=f_fin).mappings().one()
            saldo_banco = _d(corte.saldo_banco) + _d(netos["banco"])
            saldo_libros = _d(corte.saldo_libros) + _d(netos["libros"])
            pend_b, pend_e = self._pendientes_al_corte(c, desde_corte, f_fin)
            lineas = {k: {"clave": k, "nombre": n, "signo": s, "n": 0, "valor": CERO} for k, n, s in LINEAS}
            for p in corte.partidas:
                if p.estado == "PENDIENTE" and p.fecha <= f_fin:
                    lineas[p.categoria]["n"] += 1
                    lineas[p.categoria]["valor"] += _d(p.valor)
                    iniciales.append({"origen": "Corte inicial", "fecha": p.fecha, "ref": p.referencia or "",
                                      "detalle": p.detalle, "monto": _d(p.valor) * SIGNO[p.categoria]})
            for m in pend_b:   # el banco lo tiene y BC no
                k = "NOTA_DEBITO_NO_REGISTRADA" if m["monto"] < 0 else "NOTA_CREDITO_NO_REGISTRADA"
                lineas[k]["n"] += 1
                lineas[k]["valor"] += abs(_d(m["monto"]))
            for m in pend_e:   # BC lo tiene y el banco no
                k = "PENDIENTE_ACREDITAR" if m["monto"] > 0 else "PENDIENTE_DEBITAR"
                lineas[k]["n"] += 1
                lineas[k]["valor"] += abs(_d(m["monto"]))
            calc = saldo_banco + sum(l["signo"] * l["valor"] for l in lineas.values())
            ext = self._q("""
                SELECT saldo_final FROM bancos.cargas_extractos
                 WHERE id_cuenta = :c AND estado = 'Completado' AND fecha_hasta = :h AND saldo_final IS NOT NULL
              ORDER BY created_at DESC LIMIT 1
            """, c=c.id_cuenta, h=f_fin).scalar()
            cuadre = {
                "fecha": f_fin.isoformat(), "fecha_corte": corte.fecha_corte.isoformat(),
                "saldo_banco": _f(saldo_banco), "saldo_libros": _f(saldo_libros),
                "saldo_libros_calculado": _f(calc), "diferencia": _f(calc - saldo_libros),
                "saldo_extracto": _f(ext) if ext is not None else None,
                "diferencia_extracto": _f(_d(ext) - saldo_banco) if ext is not None else None,
                "lineas": [{**l, "valor": _f(l["valor"]), "ajuste": _f(l["signo"] * l["valor"])} for l in lineas.values()],
            }

        al_dia = bool(extracto_hasta) and extracto_hasta >= min(f_fin, hoy - timedelta(days=3))
        res = c.auto_match_ultimo_resultado or ""
        am_error = res.startswith("ERROR")
        diferencia = cuadre["diferencia"] if cuadre else _f(dif_mov)
        semaforo = ("bad" if (abs(diferencia) >= 0.01 or am_error)
                    else "warn" if (not al_dia or anteriores_abiertas) else "ok")
        bruto = _d(b["bruto"])
        return {
            "id_cuenta": str(c.id_cuenta), "banco": c.banco_nombre, "nombre": c.nombre_cuenta,
            "numero": c.numero_cuenta or "", "codigo_bc": c.codigo_banco_bc,
            "movimientos_banco": b["n"], "conciliados": b["n_conc"], "parciales": b["n_parcial"],
            "monto_bruto": _f(bruto), "monto_conciliado": _f(b["bruto_conc"]),
            "pct_conciliado_monto": round(float(_d(b["bruto_conc"]) / bruto * 100), 1) if bruto else None,
            "pendientes_banco": b["n_pend"], "pendiente_banco_monto": _f(b["pend"]),
            "pendientes_bc": e["n_pend"], "pendiente_bc_monto": _f(e["pend"]),
            "diferencia_movimiento": _f(dif_mov), "diferencia": diferencia,
            "banco_ingresos": _f(b["ing"]), "banco_egresos": _f(b["egr"]), "banco_n_ingresos": b["n_ing"], "banco_n_egresos": b["n_egr"],
            "bc_ingresos": _f(e["ing"]), "bc_egresos": _f(e["egr"]), "bc_n_ingresos": e["n_ing"], "bc_n_egresos": e["n_egr"],
            "extracto_hasta": extracto_hasta.isoformat() if extracto_hasta else None, "extracto_al_dia": al_dia,
            "auto_match": {"fecha": c.auto_match_ultima_ejecucion.isoformat() if c.auto_match_ultima_ejecucion else None,
                           "error": am_error, "resultado": res},
            "acta_estado": acta, "actas_anteriores_abiertas": int(anteriores_abiertas),
            "tiene_corte": corte is not None, "fecha_corte": corte.fecha_corte.isoformat() if corte else None,
            "cuadre_saldos": cuadre, "semaforo": semaforo,
            "_iniciales": iniciales,
        }

    # ------------------------------------------------------------------
    def resumen(self, desde: str, hasta: str, banco: Optional[str] = None,
                id_cuenta: Optional[UUID] = None) -> Dict[str, Any]:
        f_ini, f_fin = rango_meses(desde, hasta)
        hoy = datetime.now(ZoneInfo("America/Guayaquil")).date()
        todas = self.db.query(CuentaBancaria).filter(CuentaBancaria.activo.is_(True)).all()
        cuentas = self._cuentas(banco, id_cuenta)
        filas = [self._cuenta(c, f_ini, f_fin, hoy) for c in cuentas]

        # --- Reglas, antigüedad, causas y pendientes más antiguos ---
        reglas: Counter = Counter()
        aging = {"0-30": [0, CERO], "31-60": [0, CERO], "61+": [0, CERO]}
        causas: Counter = Counter()
        pendientes: List[Dict[str, Any]] = []
        for c, fila in zip(cuentas, filas):
            for r in self._q("""
                SELECT COALESCE(regla_cruce, 'MANUAL') AS regla, count(*) AS n FROM bancos.movimientos
                 WHERE id_cuenta = :c AND fecha_transaccion BETWEEN :d AND :h AND estado LIKE 'CONCILIADO%'
              GROUP BY 1
            """, c=c.id_cuenta, d=f_ini, h=f_fin).mappings():
                reglas[r["regla"]] += r["n"] if r["regla"] != motor.REGLA_REVERSO_BANCO else r["n"] // 2
            rev_bc = self._q("""
                SELECT count(*) FROM bancos.movimientos_erp
                 WHERE codigo_banco_bc = :cod AND posting_date BETWEEN :d AND :h AND regla_cruce = 'REGLA_REVERSO_BC'
            """, cod=c.codigo_banco_bc, d=f_ini, h=f_fin).scalar() or 0
            if rev_bc:
                reglas[motor.REGLA_REVERSO] += rev_bc // 2

            pb = [dict(r) for r in self._q("""
                SELECT id_movimiento AS id, fecha_transaccion AS fecha, numero_referencia AS ref, concepto AS detalle,
                       monto, info_adicional FROM bancos.movimientos
                 WHERE id_cuenta = :c AND fecha_transaccion BETWEEN :d AND :h AND estado IN ('Pendiente', 'NO CONCILIADO')
            """, c=c.id_cuenta, d=f_ini, h=f_fin).mappings()]
            pe = [dict(r) for r in self._q("""
                SELECT id_movimiento_erp AS id, posting_date AS fecha, document_no, external_document_no,
                       COALESCE(NULLIF(external_document_no, ''), document_no) AS ref, description AS detalle, monto
                  FROM bancos.movimientos_erp
                 WHERE codigo_banco_bc = :cod AND posting_date BETWEEN :d AND :h
                   AND (estado IS NULL OR estado IN ('Pendiente', 'NO CONCILIADO'))
            """, cod=c.codigo_banco_bc, d=f_ini, h=f_fin).mappings()]
            obs_b, obs_e = diagnostico.diagnosticar_pendientes(
                [motor.MovBanco(m["id"], m["fecha"], _d(m["monto"]),
                                motor.referencias_banco(m["ref"], m["info_adicional"]), m["ref"] or "",
                                motor.referencias_adicionales(m["info_adicional"])) for m in pb],
                [motor.MovErp(m["id"], m["fecha"], _d(m["monto"]),
                              motor.referencias_de_texto(m["document_no"], m["external_document_no"]),
                              m["document_no"] or "", motor.normalizar_referencia(m["external_document_no"])) for m in pe])
            for o in list(obs_b.values()) + list(obs_e.values()):
                causas[_causa(o)] += 1
            items = ([{"origen": "Banco", **m} for m in pb] + [{"origen": "BC", **m} for m in pe]
                     + fila.pop("_iniciales"))
            for it in items:
                dias = (f_fin - it["fecha"]).days
                tramo = "0-30" if dias <= 30 else "31-60" if dias <= 60 else "61+"
                aging[tramo][0] += 1
                aging[tramo][1] += abs(_d(it["monto"]))
                pendientes.append({"cuenta": fila["nombre"], "id_cuenta": fila["id_cuenta"], "origen": it["origen"],
                                   "fecha": it["fecha"].isoformat(), "referencia": it["ref"] or "",
                                   "detalle": (it["detalle"] or "")[:120], "monto": _f(it["monto"]), "dias": dias})
        for f in filas:
            f.pop("_iniciales", None)
        pendientes.sort(key=lambda p: (-p["dias"], -abs(p["monto"])))

        # --- Tendencia: % conciliado por monto en los últimos 6 meses del rango ---
        t_ini = date(f_fin.year, f_fin.month, 1)
        for _ in range(5):
            t_ini = (t_ini - timedelta(days=1)).replace(day=1)
        ids = [c.id_cuenta for c in cuentas]
        serie = defaultdict(lambda: [CERO, CERO])
        if ids:
            for r in self._q("""
                SELECT to_char(fecha_transaccion, 'YYYY-MM') AS p, COALESCE(sum(abs(monto)), 0) AS bruto,
                       COALESCE(sum(abs(monto)) FILTER (WHERE estado LIKE 'CONCILIADO%'), 0) AS conc
                  FROM bancos.movimientos WHERE id_cuenta = ANY(:ids) AND fecha_transaccion BETWEEN :d AND :h
              GROUP BY 1
            """, ids=ids, d=t_ini, h=f_fin).mappings():
                serie[r["p"]] = [_d(r["bruto"]), _d(r["conc"])]
        tendencia, m = [], t_ini
        while m <= f_fin:
            k = f"{m:%Y-%m}"
            bruto, conc = serie[k]
            tendencia.append({"periodo": k, "etiqueta": f"{MESES[m.month - 1]} {m:%y}",
                              "pct": round(float(conc / bruto * 100), 1) if bruto else None})
            m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)

        # --- Consolidado ---
        def suma(k):
            return round(sum(f[k] or 0 for f in filas), 2)
        bruto, conc = suma("monto_bruto"), suma("monto_conciliado")
        con_cuadre = [f for f in filas if f["cuadre_saldos"]]
        lineas = []
        for k, n, s in LINEAS:
            vals = [l for f in con_cuadre for l in f["cuadre_saldos"]["lineas"] if l["clave"] == k]
            lineas.append({"clave": k, "nombre": n, "signo": s, "n": sum(l["n"] for l in vals),
                           "valor": round(sum(l["valor"] for l in vals), 2), "ajuste": round(sum(l["ajuste"] for l in vals), 2)})
        consolidado = {
            "cuentas": len(filas),
            "movimientos_banco": sum(f["movimientos_banco"] for f in filas),
            "conciliados": sum(f["conciliados"] for f in filas), "parciales": sum(f["parciales"] for f in filas),
            "pct_conciliado_monto": round(conc / bruto * 100, 1) if bruto else None,
            "pct_parciales": round(sum(f["parciales"] for f in filas) / max(sum(f["conciliados"] for f in filas), 1) * 100, 1),
            "transito_n": sum(a[0] for a in aging.values()),
            "transito_monto": _f(sum((a[1] for a in aging.values()), CERO)),
            # Solo suma lo que no cuadra en cada cuenta: una cuenta no compensa a otra
            "diferencia_sin_explicar": round(sum(abs(f["diferencia"]) for f in filas if abs(f["diferencia"]) >= 0.01), 2),
            "cuentas_con_diferencia": sum(1 for f in filas if abs(f["diferencia"]) >= 0.01),
            "cuentas_sin_corte": sum(1 for f in filas if not f["tiene_corte"]),
            "extractos_atrasados": sum(1 for f in filas if not f["extracto_al_dia"]),
            "auto_match_con_error": sum(1 for f in filas if f["auto_match"]["error"]),
            "banco_ingresos": suma("banco_ingresos"), "banco_egresos": suma("banco_egresos"),
            "bc_ingresos": suma("bc_ingresos"), "bc_egresos": suma("bc_egresos"),
            "cuadre_saldos": {
                "cuentas": len(con_cuadre),
                "saldo_banco": round(sum(f["cuadre_saldos"]["saldo_banco"] for f in con_cuadre), 2),
                "saldo_libros": round(sum(f["cuadre_saldos"]["saldo_libros"] for f in con_cuadre), 2),
                "saldo_libros_calculado": round(sum(f["cuadre_saldos"]["saldo_libros_calculado"] for f in con_cuadre), 2),
                "diferencia": round(sum(f["cuadre_saldos"]["diferencia"] for f in con_cuadre), 2),
                "lineas": lineas,
            } if con_cuadre else None,
        }
        orden = [motor.REGLA_1, motor.REGLA_1_GYE, motor.REGLA_1N, motor.REGLA_REF_ADICIONAL, motor.REGLA_2,
                 motor.REGLA_3, motor.REGLA_REVERSO, motor.REGLA_REVERSO_BANCO]
        return {
            "filtros": {"desde": f"{f_ini:%Y-%m}", "hasta": f"{f_fin:%Y-%m}", "banco": banco,
                        "id_cuenta": str(id_cuenta) if id_cuenta else None,
                        "fecha_inicio": f_ini.isoformat(), "fecha_fin": f_fin.isoformat()},
            "bancos": sorted({c.banco_nombre for c in todas if c.banco_nombre}),
            "consolidado": consolidado,
            "cuentas": filas,
            "reglas": sorted(({"regla": k, "n": v} for k, v in reglas.items()),
                             key=lambda r: orden.index(r["regla"]) if r["regla"] in orden else 99),
            "antiguedad": [{"tramo": k, "n": v[0], "monto": _f(v[1])} for k, v in aging.items()],
            "causas": [{"causa": k, "n": v} for k, v in causas.most_common()],
            "tendencia": tendencia,
            "pendientes_antiguos": pendientes[:10],
            "generado_el": datetime.now(ZoneInfo("America/Guayaquil")).isoformat(timespec="minutes"),
        }
