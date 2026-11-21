"""El contrato versionado en docs/contrato/openapi.json debe coincidir con la app."""
import json

from scripts.exportar_openapi import SALIDA_POR_DEFECTO, generar


def test_contrato_openapi_al_dia():
    assert SALIDA_POR_DEFECTO.exists(), "Falta docs/contrato/openapi.json: python scripts/exportar_openapi.py"
    guardado = json.loads(SALIDA_POR_DEFECTO.read_text(encoding="utf-8"))
    assert guardado == generar(), (
        "docs/contrato/openapi.json está desactualizado: regenéralo con python scripts/exportar_openapi.py"
    )
