"""Pruebas del motor de cruce (sin BD).  Ejecutar: pytest -q tests/"""
from datetime import date
from decimal import Decimal as D

from app.modulos.conciliacion import motor as M

F = date(2026, 8, 10)


def banco(i, monto, ref="", dia=10, info=""):
    return M.MovBanco(i, date(2026, 8, dia), D(monto), M.referencias_banco(ref, info), ref)


def erp(i, monto, doc="", ext="", dia=10):
    return M.MovErp(i, date(2026, 8, dia), D(monto), M.referencias_de_texto(doc, ext))


def reglas(res):
    return {c.id_banco: (c.regla, sorted(c.ids_erp)) for c in res.cruces}


def test_normalizar_referencia():
    assert M.normalizar_referencia(" 0068745486 ") == "68745486"
    assert M.normalizar_referencia("0") == ""
    assert "43892576" in M.referencias_banco("1745383506", "REF2: 713.00-43892576 / OF: MATRIZ")


def test_r1_por_referencia_y_signo():
    r = M.ejecutar_motor([banco("b1", "-713.00", "1745383506")],
                         [erp("e0", "713.00", ext="1745383506"),     # signo opuesto: no cruza
                          erp("e1", "-713.00", ext="1745383506")])
    assert reglas(r) == {"b1": (M.REGLA_1, ["e1"])}


def test_r1_ref2_en_info_adicional():
    r = M.ejecutar_motor([banco("b1", "1211.44", "885777", info="REF2: 010000000454949")],
                         [erp("e1", "1211.44", ext="10000000454949", dia=12)])
    assert reglas(r)["b1"][0] == M.REGLA_1


def test_r1n_varios_asientos_misma_referencia():
    r = M.ejecutar_motor([banco("b1", "300.00", "REF999")],
                         [erp("e1", "100.00", ext="REF999"), erp("e2", "200.00", ext="REF999")])
    assert reglas(r) == {"b1": (M.REGLA_1N, ["e1", "e2"])}


def test_r2_monto_fecha_sin_ambiguedad():
    r = M.ejecutar_motor([banco("b1", "55.10")], [erp("e1", "55.10", dia=12)])
    assert reglas(r) == {"b1": (M.REGLA_2, ["e1"])}


def test_r2_ambiguo_queda_pendiente():
    r = M.ejecutar_motor([banco("b1", "60.00"), banco("b2", "60.00", dia=11)], [erp("e1", "60.00", dia=10)])
    assert r.cruces == []


def test_fuera_de_ventana_no_cruza():
    r = M.ejecutar_motor([banco("b1", "55.10")], [erp("e1", "55.10", dia=20)])
    assert r.cruces == []


def test_r3_suma_unica():
    r = M.ejecutar_motor([banco("b1", "1000.00")], [erp("e1", "400.00"), erp("e2", "600.00", dia=11),
                                                     erp("e3", "-600.00")])
    assert reglas(r) == {"b1": (M.REGLA_3, ["e1", "e2"])}


def test_r5_ref_adicional_debito_fuera_de_ventana():
    # Caso real: el banco debita el 08/10 (REF 685880359 en info adicional) y BC registra el 12/10
    b = M.MovBanco("b1", date(2026, 10, 8), D("-2414.97"),
                   M.referencias_banco("1008676445880", "TIPO: Nota de débito / AG: Matriz / REF: 685880359 / REF3: Pago proveedores"),
                   "1008676445880", M.referencias_adicionales("TIPO: Nota de débito / AG: Matriz / REF: 685880359 / REF3: Pago proveedores"))
    e = M.MovErp("e1", date(2026, 10, 12), D("-2414.97"), M.referencias_de_texto("PS02-1", "685880359"),
                 "PS02-1", M.normalizar_referencia("685880359"))
    r = M.ejecutar_motor([b], [e])
    assert reglas(r) == {"b1": (M.REGLA_REF_ADICIONAL, ["e1"])}


def test_r5_no_aplica_a_creditos_ni_fuera_de_ventana_amplia():
    info = "REF: 555123"
    b_cred = M.MovBanco("b1", date(2026, 10, 1), D("100.00"), M.referencias_banco("1", info), "1", M.referencias_adicionales(info))
    b_lejos = M.MovBanco("b2", date(2026, 10, 1), D("-50.00"), M.referencias_banco("2", info), "2", M.referencias_adicionales(info))
    e1 = M.MovErp("e1", date(2026, 10, 9), D("100.00"), set(), "D1", "555123")
    e2 = M.MovErp("e2", date(2026, 10, 30), D("-50.00"), set(), "D2", "555123")
    assert M.ejecutar_motor([b_cred, b_lejos], [e1, e2]).cruces == []


def test_reversos_bc_se_anulan_y_no_cruzan_con_el_banco():
    f = date(2026, 10, 5)
    e1 = M.MovErp("e1", f, D("336.33"), set(), "PS28-000000418")
    e2 = M.MovErp("e2", f, D("-336.33"), set(), "PS28-000000418")
    e3 = M.MovErp("e3", f, D("-336.33"), set(), "PS28-000000418")  # sobra: queda pendiente
    b = M.MovBanco("b1", f, D("336.33"), set())                       # no debe cruzar con e1
    r = M.ejecutar_motor([b], [e1, e2, e3])
    assert r.reversos == [("e2", "e1")]
    assert "e1" not in {i for c in r.cruces for i in c.ids_erp}


def test_reversos_requieren_misma_fecha_y_documento():
    a = M.MovErp("a", date(2026, 10, 5), D("10.00"), set(), "DOC1")
    b = M.MovErp("b", date(2026, 10, 6), D("-10.00"), set(), "DOC1")
    c = M.MovErp("c", date(2026, 10, 5), D("-10.00"), set(), "DOC2")
    assert M.detectar_reversos([a, b, c]) == []
