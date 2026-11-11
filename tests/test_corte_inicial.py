"""Cuadre del corte inicial (caso real: Banco Guayaquil, septiembre 2026)."""
from datetime import date
from decimal import Decimal as D

from app.modulos.conciliacion.corte_inicial import saldo_libros_calculado
from app.modulos.conciliacion.dashboard import _causa
from app.modulos.conciliacion.schemas import PartidaInicialIn


def p(cat, valor):
    return PartidaInicialIn(categoria=cat, fecha=date(2026, 9, 30), detalle="x", valor=D(valor))


def test_corte_guayaquil_septiembre_cuadra():
    partidas = [p("CHEQUE_NO_COBRADO", "910.32"), p("NOTA_DEBITO_NO_REGISTRADA", "33000"),
                p("NOTA_DEBITO_NO_REGISTRADA", "6163.96")]
    assert saldo_libros_calculado(D("287078.92"), partidas) == D("325332.56")


def test_signos_de_cada_categoria():
    base = D("1000")
    assert saldo_libros_calculado(base, [p("NOTA_CREDITO_NO_REGISTRADA", "10")]) == D("990")
    assert saldo_libros_calculado(base, [p("PENDIENTE_ACREDITAR", "10")]) == D("1010")
    assert saldo_libros_calculado(base, [p("PENDIENTE_DEBITAR", "10")]) == D("990")


def test_causas_del_dashboard():
    assert _causa("Ambiguo: 2 asientos de BC con el mismo monto") == "Ambiguo"
    assert _causa("Referencia 77777 y monto coinciden con BC ING-10 del 27/09/2026, pero a 7 días (ventana ±7).") == "Fuera de ventana"
    assert _causa("Referencia 55555 encontrada en BC PAG-20 con otro monto (-$40.00; banco -$45.00).") == "Otro monto"
    assert _causa("Sin candidato en BC: ninguna referencia ni monto coincidente en el periodo.") == "Sin contrapartida"


def test_auto_match_exige_mes_completo():
    import pytest
    from fastapi import HTTPException
    from app.modulos.conciliacion.service import ConciliacionService

    svc = ConciliacionService(db=None)
    with pytest.raises(HTTPException) as e:
        svc.ejecutar_conciliacion_periodo(None, "2026-08", date(2026, 8, 15), date(2026, 9, 15), "x")
    assert e.value.status_code == 422 and "mes completo" in e.value.detail
