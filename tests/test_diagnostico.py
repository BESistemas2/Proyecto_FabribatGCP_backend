"""Pruebas del diagnóstico del reporte 'Detalle de cruces' (sin BD)."""
from datetime import date
from decimal import Decimal as D

from app.modulos.conciliacion import diagnostico as G
from app.modulos.conciliacion import motor as M


def banco(i, monto, ref="", dia=10, info=""):
    return M.MovBanco(i, date(2026, 8, dia), D(monto), M.referencias_banco(ref, info), ref,
                      M.referencias_adicionales(info))


def erp(i, monto, doc="", ext="", dia=10):
    return M.MovErp(i, date(2026, 8, dia), D(monto), M.referencias_de_texto(doc, ext), doc,
                    M.normalizar_referencia(ext))


def diag(bs, es, bc=(), ec=()):
    return G.diagnosticar_pendientes(bs, es, bc, ec)


def test_referencia_coincidente_r1_y_r5():
    b = banco("b", "-50", "No.0068745486")
    e = erp("e", "-50", "PAG-1", "68745486")
    assert G.referencia_coincidente(b, e, M.REGLA_1) == "68745486"
    b5 = banco("b5", "-20", "", info="REF: OC-77 / OF: MATRIZ")
    e5 = erp("e5", "-20", "PAG-9", "OC-77")
    assert G.referencia_coincidente(b5, e5, M.REGLA_REF_ADICIONAL) == "OC77"
    assert G.referencia_coincidente(banco("x", "10"), erp("y", "10"), M.REGLA_2) == G.SIN_REFERENCIA


def test_referencia_fuera_de_ventana():
    ob, oe = diag([banco("b", "100", "12345", dia=1)], [erp("e", "100", "12345", dia=9)])
    assert "a 8 días" in ob["b"] and "±3" in ob["b"]
    assert "a 8 días" in oe["e"]


def test_referencia_con_otro_monto_y_1n_incompleto():
    ob, _ = diag([banco("b", "100", "55555")], [erp("e", "90", "55555")])
    assert "otro monto" in ob["b"]
    ob, _ = diag([banco("b", "100", "55555")], [erp("e1", "40", "55555"), erp("e2", "50", "55555")])
    assert "2 asientos" in ob["b"] and "$90.00" in ob["b"]


def test_signo_contrario():
    ob, _ = diag([banco("b", "100", "77777")], [erp("e", "-100", "77777")])
    assert "signo contrario" in ob["b"]


def test_ya_conciliado_con_otro():
    ob, _ = diag([banco("b", "100", "88888")], [], ec=[erp("x", "100", "88888")])
    assert "ya conciliado" in ob["b"]


def test_ambiguo_por_monto_y_sin_candidato():
    ob, oe = diag([banco("b", "60")], [erp("e1", "60", dia=9), erp("e2", "60", dia=11)])
    assert ob["b"].startswith("Ambiguo: 2 asientos")
    ob, _ = diag([banco("b1", "60"), banco("b2", "60", dia=11)], [erp("e", "60")])
    assert "compite" in ob["b1"]
    ob, oe = diag([banco("b", "1.23")], [erp("e", "9.99")])
    assert ob["b"].startswith("Sin candidato")
    assert "tránsito" in oe["e"]
