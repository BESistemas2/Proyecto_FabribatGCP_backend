import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests

from app.core.bc_auth import get_oauth_token, resolve_company_info
from app.core.config import BC_CONFIG

logger = logging.getLogger(__name__)

PREFIJO_CUENTAS_GL_BANCOS = "1.01.01.02.01."
TIMEOUT = 25


class BancosBCService:
    """Lectura del catálogo de cuentas bancarias desde Dynamics 365 Business Central."""

    @staticmethod
    def _odata_escape(valor: str) -> str:
        return valor.replace("'", "''")

    @classmethod
    def _grupo_registro_por_banco(
        cls, session: requests.Session, company_name: str, codigos_banco: List[str]
    ) -> Dict[str, str]:
        """
        Grupo de registro contable (BankAccPostingGroup) de cada banco, leído del cubo OData.
        Se consulta 1 movimiento por banco: antes se leían solo los primeros 500 movimientos
        del libro y los bancos con poca actividad quedaban sin grupo.
        """
        url = (f"{BC_CONFIG['odata_url_prd']}/Company('{quote(company_name)}')"
               f"/GmasBankAccountLedgerEntr_Cubo")
        mapa: Dict[str, str] = {}
        for codigo in codigos_banco:
            params = {
                "$select": "BankAccountNo,BankAccPostingGroup",
                "$filter": f"BankAccountNo eq '{cls._odata_escape(codigo)}'",
                "$top": "1",
            }
            try:
                res = session.get(url, params=params, timeout=TIMEOUT)
                if not res.ok:
                    logger.warning("Cubo BC %s para %s: HTTP %s", url, codigo, res.status_code)
                    continue
                valores = res.json().get("value", [])
                grupo = str((valores[0] if valores else {}).get("BankAccPostingGroup") or "").strip()
                if grupo:
                    mapa[codigo] = grupo
            except requests.RequestException:
                logger.exception("Error leyendo grupo de registro del banco %s", codigo)
        return mapa

    @staticmethod
    def _cuentas_plan_contable(session: requests.Session, base_url: str) -> List[Dict[str, str]]:
        """Cuentas de detalle de BANCOS (1.01.01.02.01.XX) del plan de cuentas."""
        try:
            res = session.get(f"{base_url}/accounts", timeout=TIMEOUT)
            res.raise_for_status()
        except requests.RequestException:
            logger.exception("Error consultando el plan de cuentas de BC")
            return []

        cuentas = []
        for acc in res.json().get("value", []):
            numero = str(acc.get("number") or "").strip()
            nombre = str(acc.get("displayName") or acc.get("name") or "").strip()
            if numero.startswith(PREFIJO_CUENTAS_GL_BANCOS):
                cuentas.append({
                    "account_number": numero,
                    "display_name": nombre,
                    "digits_only": re.sub(r"\D", "", nombre),
                })
        return cuentas

    @staticmethod
    def _buscar_cuenta_contable(num_cta: str, grupo_bc: Optional[str], cuentas_gl: List[Dict[str, str]]) -> Optional[str]:
        """Cruza el nº de cuenta bancaria (o el grupo) con la descripción de la cuenta contable."""
        digitos = re.sub(r"\D", "", num_cta or "")
        if len(digitos) >= 4:
            sin_ceros = digitos.lstrip("0")
            for gl in cuentas_gl:
                if digitos in gl["digits_only"] or (sin_ceros and sin_ceros in gl["digits_only"]):
                    return gl["account_number"]
        if grupo_bc:
            grupo = grupo_bc.upper().replace("BANCO", "").replace("BCO", "").strip()
            for gl in cuentas_gl:
                if grupo and grupo in gl["display_name"].upper():
                    return gl["account_number"]
        return None

    @classmethod
    def sincronizar_catalogo_cuentas_bc(cls) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Devuelve (cuentas, error). El error es un mensaje apto para el usuario;
        el detalle técnico queda en el log.
        """
        token, err_token = get_oauth_token()
        if err_token:
            logger.error("Autenticación con Azure AD fallida: %s", err_token)
            return [], "No se pudo autenticar con Business Central."

        comp_info, err_company = resolve_company_info(token)
        if err_company:
            logger.error("No se pudo resolver la empresa en BC: %s", err_company)
            return [], "No se pudo identificar la empresa en Business Central."
        company_id, company_name = comp_info

        base_url = f"{BC_CONFIG['base_url_prd']}/companies({company_id})"
        with requests.Session() as session:
            session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})
            try:
                res = session.get(f"{base_url}/bankAccounts", timeout=TIMEOUT)
                res.raise_for_status()
            except requests.RequestException:
                logger.exception("Error consultando bankAccounts en BC")
                return [], "Business Central no respondió al consultar las cuentas bancarias."

            bancos = [b for b in res.json().get("value", [])
                      if str(b.get("number") or "").strip().upper().startswith("B")]
            codigos = [str(b["number"]).strip() for b in bancos]
            grupos = cls._grupo_registro_por_banco(session, company_name, codigos)
            cuentas_gl = cls._cuentas_plan_contable(session, base_url)

        cuentas = []
        for bank in bancos:
            codigo = str(bank.get("number")).strip()
            num_cta = str(bank.get("bankAccountNumber") or "").strip()
            grupo = grupos.get(codigo)
            cuentas.append({
                "codigo_banco_bc": codigo,
                "nombre_cuenta": str(bank.get("displayName") or bank.get("name") or "").strip(),
                "numero_cuenta": num_cta or None,
                "grupo_registro_bc": grupo,
                "cuenta_contable_bc": cls._buscar_cuenta_contable(num_cta, grupo, cuentas_gl),
                "moneda": str(bank.get("currencyCode") or "USD").strip() or "USD",
            })
        return cuentas, None
