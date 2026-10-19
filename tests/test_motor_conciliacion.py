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
