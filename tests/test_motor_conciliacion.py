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


def test_r5_no_usa_ref3():
    # El documento externo de BC coincide solo con la REF3 del banco: la R5 no debe cruzar
    info = "TIPO: Nota de débito / AG: Matriz / REF: 111222 / REF3: 3584"
    b = M.MovBanco("b1", date(2026, 10, 1), D("-75.00"), M.referencias_banco("1", info), "1", M.referencias_adicionales(info))
    e = M.MovErp("e1", date(2026, 10, 12), D("-75.00"), set(), "PAG-1", "3584")
    assert M.referencias_adicionales(info) == {"111222"}
    assert M.ejecutar_motor([b], [e]).cruces == []


def _gye(i, dia, monto, ref_principal, concepto_ref):
    info = f"TIPO: Nota / AG: Matriz / REF: {concepto_ref} / REF2: Empresa electrica publica estrategica co / REF3: 77"
    return M.MovBanco(i, date(2026, 10, dia), D(monto), M.referencias_banco(ref_principal, info), ref_principal,
                      M.referencias_adicionales(info), M.claves_reverso_banco(info))


def caso_cnel():
    """Caso real Banco Guayaquil 19/10/2026: dos pagos de servicio (28,95 + comisión 0,27 + IVA 0,04) y un reverso."""
    return [
        _gye("d1", 19, "-0.27", "1019550156766", "200017439353"),
        _gye("d2", 19, "-0.04", "1019550156766", "200017439353"),
        _gye("d3", 19, "-28.95", "1019574394276", "200017439353"),
        _gye("d4", 19, "-0.27", "1019985668422", "200017439353"),
        _gye("d5", 19, "-0.04", "1019985668422", "200017439353"),
        _gye("d6", 19, "-28.95", "1019592178393", "200017439353"),
        _gye("c1", 19, "28.95", "1019189448334", "Reverso -  200017439353"),
        _gye("c2", 19, "0.27", "1019449763125", "Reverso -  200017439353"),
        _gye("c3", 19, "0.04", "1019449763125", "Reverso -  200017439353"),
    ]


def test_claves_reverso_banco_toma_el_numero_de_ref():
    assert M.claves_reverso_banco("REF: Reverso -  200017439353 / REF2: Empresa / REF3: 0012345678") == {"200017439353"}


def test_reverso_banco_guayaquil_anula_tres_pares_y_deja_un_pago():
    movs = caso_cnel()
    bc = M.MovErp("cpd", date(2026, 10, 19), D("-28.95"), M.referencias_de_texto("CPD012388", ""), "CPD012388")
    r = M.ejecutar_motor(movs, [bc], es_banco_guayaquil=True)
    pares = r.reversos_banco
    assert len(pares) == 3
    anulados = {i for p in pares for i in p}
    assert {"c1", "c2", "c3"} <= anulados
    restantes = {m.id for m in movs} - anulados
    # Queda un pago completo: 28,95 + 0,27 + 0,04
    assert sorted(str(next(m.monto for m in movs if m.id == i)) for i in restantes) == ["-0.04", "-0.27", "-28.95"]
    # El pago de 28,95 que queda ya no es ambiguo y cruza con BC por monto + fecha
    pago = next(i for i in restantes if next(m.monto for m in movs if m.id == i) == D("-28.95"))
    assert reglas(r) == {pago: (M.REGLA_2, ["cpd"])}


def test_reverso_banco_solo_guayaquil_y_mismo_dia():
    movs = caso_cnel()
    assert M.ejecutar_motor(movs, [], es_banco_guayaquil=False).reversos_banco == []
    d = _gye("d", 19, "-10.00", "1", "200017439353")
    c = _gye("c", 20, "10.00", "2", "Reverso - 200017439353")
    otra_ref = _gye("x", 19, "10.00", "3", "Reverso - 999999999")
    assert M.detectar_reversos_banco([d, c, otra_ref]) == []
