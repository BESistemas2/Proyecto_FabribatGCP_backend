"""Gastos bancarios: clasificación por patrones y cruce N a 1 con el asiento mensual."""
from datetime import date
from decimal import Decimal as D

from app.modulos.conciliacion import diagnostico as G
from app.modulos.conciliacion import motor as M
from app.modulos.conciliacion.gastos_bancarios import Patron, clasificar, patrones_para_banco, validar_patron

PATRONES = [
    Patron("GUAYAQUIL", "COM.PAGO SERVICIOS", "EMPIEZA", "Comisión"),
    Patron("GUAYAQUIL", "COMIS.SWIFT", "EMPIEZA", "Comisión"),
    Patron("GUAYAQUIL", "COMISION CH.DEVUELTO", "EMPIEZA", "Comisión"),
    Patron("GUAYAQUIL", "COMISION FINANC. VOUCHER", "EMPIEZA", "Comisión"),
    Patron("GUAYAQUIL", "^IMP [0-9]+% IVA", "REGEX", "IVA"),
    Patron("GUAYAQUIL", "^TARIFA PAGO ?/ ?NOMINA", "REGEX", "Tarifa"),
    Patron("PICHINCHA", "COSTO IVA CASH-RECAUDACIONES", "CONTIENE", "IVA"),
    Patron("PICHINCHA", "IVA COBRADO", "EMPIEZA", "IVA"),
    Patron("PICHINCHA", "-COMISION-PAG-", "CONTIENE", "Comisión"),
]
GYE = patrones_para_banco(PATRONES, "Banco de Guayaquil")
PCH = patrones_para_banco(PATRONES, "BANCO PICHINCHA")


def test_patrones_por_banco():
    assert {p.banco for p in GYE} == {"GUAYAQUIL"} and {p.banco for p in PCH} == {"PICHINCHA"}


def test_clasificacion_guayaquil():
    assert clasificar("Com.pago servicios", D("-0.45"), GYE) == "Comisión"
    assert clasificar("Comis.swift america proveedores", D("-25"), GYE) == "Comisión"
    assert clasificar("Imp 15% IVA iva servicios", D("-0.07"), GYE) == "IVA"
    assert clasificar("Imp 12% IVA iva servicios", D("-0.07"), GYE) == "IVA"
    assert clasificar("Tarifa pago / nómina", D("-1.00"), GYE) == "Tarifa"
    assert clasificar("Tarifa pago/nomina", D("-1.00"), GYE) == "Tarifa"
    assert clasificar("Comision ch.devuelto transferido", D("-2.49"), GYE) == "Comisión"
    assert clasificar("Comision financ. voucher", D("-11.56"), GYE) == "Comisión"
    assert clasificar("Transferencia recibida", D("-10"), GYE) is None


def test_clasificacion_pichincha_y_solo_debitos():
    assert clasificar("2605060BSJL8-COMISION-PAG-1791398262001", D("-0.30"), PCH) == "Comisión"
    assert clasificar("COSTO IVA CASH-RECAUDACIONES-1791398262001", D("-0.05"), PCH) == "IVA"
    assert clasificar("IVA COBRADO", D("-0.05"), PCH) == "IVA"
    assert clasificar("IVA COBRADO", D("0.05"), PCH) is None          # un crédito nunca es gasto


def test_validar_patron():
    assert validar_patron("^IMP [0-9", "REGEX").startswith("Expresión regular inválida")
    assert validar_patron("X", "OTRO").startswith("Tipo de coincidencia inválido")
    assert validar_patron("IVA COBRADO", "EMPIEZA") is None


def gasto(i, monto, dia, mes=9):
    return M.MovBanco(i, date(2026, mes, dia), D(monto), set(), "", gasto_bancario=True)


def erp(i, monto, dia, mes=9, doc=""):
    return M.MovErp(i, date(2026, mes, dia), D(monto), set(), doc)


def test_gastos_del_mes_cruzan_con_el_asiento_por_el_total():
    banco = [gasto("g1", "-0.45", 3), gasto("g2", "-0.07", 3), gasto("g3", "-25.00", 18)]
    r = M.ejecutar_motor(banco, [erp("a1", "-25.52", 30, doc="GB-0926")])
    assert len(r.gastos) == 1
    g = r.gastos[0]
    assert (g.mes, sorted(g.ids_banco), g.ids_erp, g.total) == ("2026-09", ["g1", "g2", "g3"], ["a1"], D("-25.52"))
    assert r.cruces == []


def test_asiento_fuera_de_los_ultimos_dias_o_con_otro_total_no_cruza():
    banco = [gasto("g1", "-0.45", 3), gasto("g2", "-0.07", 3)]
    assert M.ejecutar_motor(banco, [erp("a1", "-0.52", 24)]).gastos == []      # antes del día 25
    assert M.ejecutar_motor(banco, [erp("a1", "-0.52", 2, mes=10)]).gastos == []  # mes siguiente
    assert M.ejecutar_motor(banco, [erp("a1", "-0.50", 30)]).gastos == []      # otro total


def test_gastos_sin_asiento_no_pasan_por_r2():
    # Sin la exclusión, R2 cruzaría la comisión con un asiento de BC del mismo monto y fecha
    r = M.ejecutar_motor([gasto("g1", "-0.45", 3)], [erp("e1", "-0.45", 3)])
    assert r.cruces == [] and r.gastos == []


def test_dos_asientos_posibles_queda_pendiente():
    banco = [gasto("g1", "-1.00", 3)]
    assert M.ejecutar_motor(banco, [erp("a1", "-1.00", 28), erp("a2", "-1.00", 30)]).gastos == []


def test_diagnostico_de_gasto_pendiente():
    banco = [gasto("g1", "-0.45", 3), gasto("g2", "-0.07", 4)]
    ob, _ = G.diagnosticar_pendientes(banco, [])
    assert ob["g1"].startswith("Gasto bancario") and "-$0.52" in ob["g1"]


def test_gasto_registrado_en_bc_con_su_referencia_cruza_por_r1_y_sale_del_total():
    banco = [M.MovBanco("g1", date(2026, 9, 3), D("-529.22"), {"5551234"}, "5551234", gasto_bancario=True),
             gasto("g2", "-0.45", 3), gasto("g3", "-0.07", 3)]
    erps = [M.MovErp("e1", date(2026, 9, 3), D("-529.22"), {"5551234"}), erp("a1", "-0.52", 30)]
    r = M.ejecutar_motor(banco, erps)
    assert [(c.id_banco, c.regla) for c in r.cruces] == [("g1", M.REGLA_1)]
    assert len(r.gastos) == 1 and sorted(r.gastos[0].ids_banco) == ["g2", "g3"] and r.gastos[0].ids_erp == ["a1"]


def test_factura_del_banco_mas_nota_de_credito():
    # Caso real (Guayaquil, octubre 2026): gastos del mes -1483.96; BC registra la factura del banco
    # -1484.27 (incluye comisión e IVA reversados) y una nota de crédito +0.31 que los compensa.
    banco = [gasto("g1", "-1483.56", 3, mes=10), gasto("g2", "-0.20", 12, mes=10), gasto("g3", "-0.20", 12, mes=10)]
    erps = [erp("fac", "-1484.27", 30, mes=10), erp("nc", "0.31", 30, mes=10),
            erp("otro", "-250.00", 28, mes=10), erp("dep", "3000.00", 29, mes=10)]
    r = M.ejecutar_motor(banco, erps)
    assert len(r.gastos) == 1 and sorted(r.gastos[0].ids_erp) == ["fac", "nc"]


def test_factura_con_ajuste_ambiguo_queda_pendiente():
    banco = [gasto("g1", "-100.00", 3)]
    erps = [erp("fac", "-100.50", 30), erp("nc1", "0.50", 30), erp("nc2", "0.50", 29)]
    assert M.ejecutar_motor(banco, erps).gastos == []


def test_ajuste_grande_no_se_acepta():
    # Un "ajuste" de más del 5% del total no es una nota de crédito de la factura
    banco = [gasto("g1", "-100.00", 3)]
    assert M.ejecutar_motor(banco, [erp("fac", "-150.00", 30), erp("x", "50.00", 30)]).gastos == []
