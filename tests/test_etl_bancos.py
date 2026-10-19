"""
Pruebas del ETL de extractos (sin BD ni red).
Ejecutar:  pip install pytest  &&  pytest -q tests/
"""
from datetime import date
from decimal import Decimal

import pytest

from app.modulos.bancos.etl import EtlError, parse_fecha, parse_monto, procesar_extracto


@pytest.mark.parametrize("entrada, esperado", [
    ("$1,211.44", "1211.44"), ("$ 713.00", "713.00"), ("622.64", "622.64"),
    ("1.234,56", "1234.56"), ("(100.00)", "-100.00"), ("1,234", "1234.00"),
    ("12,50", "12.50"), ("-5", "-5.00"), (0.07, "0.07"), ("$405,124.75", "405124.75"),
])
def test_parse_monto(entrada, esperado):
    assert parse_monto(entrada) == Decimal(esperado)


def test_parse_monto_invalido():
    with pytest.raises(ValueError):
        parse_monto("abc")
    assert parse_monto("") is None


def test_parse_fecha():
    assert parse_fecha("03/04/2026") == date(2026, 4, 3)       # dd/mm/yyyy
    assert parse_fecha("2026-08-31") == date(2026, 8, 31)
    assert parse_fecha("2026-08-03 09:56:00") == date(2026, 8, 3)


PICHINCHA_CSV = (
    "Fecha,Documento,Concepto,Tipo,Oficina,Monto,Codigo,Saldo\n"
    "31/08/2026,190850828,TRANSFERENCIA RECIBIDA,C,AG. NORTE,622.64,001045,1622.64\n"
    "30/08/2026,975827,COSTO IVA CASH,D,AG. NORTE,0.48,001055,1000.00\n"
).encode()


def test_pichincha_signos_y_saldos():
    r = procesar_extracto(PICHINCHA_CSV, "pichincha.csv", "BANCO PICHINCHA")
    assert r.banco == "PICHINCHA"
    assert [m.monto for m in r.movimientos] == [Decimal("622.64"), Decimal("-0.48")]
    assert [m.tipo for m in r.movimientos] == ["CREDITO", "DEBITO"]
    assert r.saldo_inicial == Decimal("1000.48") and r.saldo_final == Decimal("1622.64")
    assert r.advertencias == []


def test_banco_equivocado():
    with pytest.raises(EtlError):
        procesar_extracto(PICHINCHA_CSV, "pichincha.csv", "BANCO GUAYAQUIL")


def test_formato_no_soportado():
    with pytest.raises(EtlError):
        procesar_extracto(b"x", "extracto.pdf")
