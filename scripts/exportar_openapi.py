"""Exporta el contrato OpenAPI de la API sin levantar el servidor ni conectar a servicios.

Uso (desde la raíz del repo):
    python scripts/exportar_openapi.py [ruta_salida]

Por defecto escribe docs/contrato/openapi.json. Usa los valores ficticios de
tests/conftest.py: ningún valor real hace falta ni se lee.
"""
import json
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
SALIDA_POR_DEFECTO = RAIZ / "docs" / "contrato" / "openapi.json"


def _preparar_entorno() -> None:
    """Variables ficticias antes de importar la app (config.py valida al importarse)."""
    if str(RAIZ) not in sys.path:
        sys.path.insert(0, str(RAIZ))
    # Vacía a propósito: si un .env local la trajera, config.py intentaría usar Secret Manager.
    # load_dotenv(override=False) no pisa una variable que ya existe en el entorno.
    os.environ["GCP_PROJECT_ID"] = ""
    import tests.conftest  # noqa: F401  - pone los valores ficticios con setdefault


def generar() -> dict:
    """Esquema OpenAPI de app.main:app. No ejecuta el arranque (lifespan) ni abre conexiones."""
    _preparar_entorno()
    from app.main import app

    return app.openapi()


def a_texto(esquema: dict) -> str:
    """Formato estable del archivo versionado."""
    return json.dumps(esquema, ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    salida = Path(sys.argv[1]) if len(sys.argv) > 1 else SALIDA_POR_DEFECTO
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(a_texto(generar()), encoding="utf-8", newline="\n")
    print(f"Contrato OpenAPI escrito en {salida}")


if __name__ == "__main__":
    main()
