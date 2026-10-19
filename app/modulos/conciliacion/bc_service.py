import logging
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests

from app.core.bc_auth import get_oauth_token
from app.core.config import BC_CONFIG

logger = logging.getLogger(__name__)

TIMEOUT = 30
MAX_PAGINAS = 200  # protección ante un bucle infinito de @odata.nextLink
CAMPOS = ("EntryNo,BankAccountNo,BankAccPostingGroup,PostingDate,DocumentNo,"
          "ExternalDocumentNo,Description,DebitAmount,CreditAmount,Amount")


def _odata_str(valor: str) -> str:
    """Literal de texto OData con comillas escapadas."""
    return "'" + str(valor).replace("'", "''") + "'"


def _decimal(v: Any) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal("0.01"))


class BusinessCentralReaderService:
    """Lectura del libro de bancos (Bank Account Ledger Entries) desde el cubo OData de BC."""

    @classmethod
    def consultar_mayor_bancos(
        cls, fecha_inicio: date, fecha_fin: date, codigo_banco_bc: str
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Movimientos de UNA cuenta bancaria de BC (BankAccountNo) entre dos fechas.
        Sigue la paginación de OData para no perder registros en periodos grandes.
        Devuelve (movimientos, error). El error es un mensaje apto para el usuario.
        """
        if not codigo_banco_bc:
            return [], "La cuenta bancaria no tiene código de Business Central."

        token, err_token = get_oauth_token()
        if err_token:
            return [], "No se pudo autenticar con Business Central."

        url = f"{BC_CONFIG['odata_url_prd']}/Company('{quote(BC_CONFIG['company_name'])}')/GmasBankAccountLedgerEntr_Cubo"
        params: Optional[Dict[str, str]] = {
            "$filter": (f"PostingDate ge {fecha_inicio.isoformat()} and PostingDate le {fecha_fin.isoformat()}"
                        f" and BankAccountNo eq {_odata_str(codigo_banco_bc)}"),
            "$select": CAMPOS,
        }

        entradas: List[Dict[str, Any]] = []
        with requests.Session() as session:
            session.headers.update({
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Prefer": "odata.maxpagesize=5000",
            })
            for _ in range(MAX_PAGINAS):
                try:
                    res = session.get(url, params=params, timeout=TIMEOUT)
                except requests.Timeout:
                    logger.warning("Timeout consultando BC (%s)", codigo_banco_bc)
                    return [], "Business Central no respondió a tiempo."
                except requests.RequestException:
                    logger.exception("Error de red consultando BC")
                    return [], "No se pudo conectar con Business Central."
                if not res.ok:
                    logger.error("BC OData %s: %s", res.status_code, res.text[:500])
                    return [], f"Business Central respondió con error ({res.status_code})."

                cuerpo = res.json()
                entradas.extend(cuerpo.get("value", []))
                url = cuerpo.get("@odata.nextLink")
                params = None  # nextLink ya trae los parámetros
                if not url:
                    break
            else:
                logger.error("Se alcanzó el máximo de páginas de BC para %s", codigo_banco_bc)

        normalizados = []
        for item in entradas:
            if item.get("EntryNo") is None:
                continue
            doc_no = str(item.get("DocumentNo") or "").strip()
            ext_doc = str(item.get("ExternalDocumentNo") or "").strip()
            normalizados.append({
                "entry_no_bc": int(item["EntryNo"]),
                "codigo_banco_bc": str(item.get("BankAccountNo") or "").strip(),
                "grupo_registro_bc": str(item.get("BankAccPostingGroup") or "").strip(),
                "posting_date": str(item.get("PostingDate") or "").split("T")[0],
                "document_no": doc_no,
                "external_document_no": ext_doc,
                "description": str(item.get("Description") or "")[:255],
                "debit_amount": _decimal(item.get("DebitAmount")),
                "credit_amount": _decimal(item.get("CreditAmount")),
                # Amount de BC: positivo = entra dinero al banco (igual convención que el extracto)
                "monto": _decimal(item.get("Amount")),
            })
        return normalizados, None
