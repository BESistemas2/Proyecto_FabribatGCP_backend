import re
import requests
from typing import List, Dict, Any, Tuple, Optional
from urllib.parse import quote

from app.core.config import AZURE_CONFIG, BC_ENV, BC_CONFIG, BC_API_BASE
from app.core.bc_auth import get_oauth_token, resolve_company_info


class BancosBCService:

    @classmethod
    def _obtener_mapa_grupos_cubo(cls, token: str) -> Dict[str, str]:
        """
        Extrae del Cubo OData V4 los grupos de registro contable (BankAccPostingGroup).
        """
        mapa_grupos = {}
        try:
            company_name = BC_CONFIG.get('company_name', 'FABRIBAT')
            company_param = quote(company_name)
            tenant_id = AZURE_CONFIG.get('tenant_id')
            env_prd = BC_ENV.get('prd', 'Production')

            odata_url = (
                f"https://api.businesscentral.dynamics.com/v2.0/{tenant_id}/{env_prd}"
                f"/ODataV4/Company('{company_param}')/GmasBankAccountLedgerEntr_Cubo"
                f"?$select=BankAccountNo,BankAccPostingGroup&$top=500"
            )

            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json"
            }

            res = requests.get(odata_url, headers=headers, timeout=25)
            if res.ok:
                for item in res.json().get("value", []):
                    banco_no = str(item.get("BankAccountNo") or "").strip()
                    posting_group = str(item.get("BankAccPostingGroup") or "").strip()
                    if banco_no and posting_group and banco_no.upper().startswith("B"):
                        mapa_grupos[banco_no] = posting_group

        except Exception as e:
            print(f"⚠️ Advertencia al leer grupos del cubo: {str(e)}")

        return mapa_grupos

    @classmethod
    def _obtener_cuentas_plan_contable(cls, token: str, company_id: str) -> List[Dict[str, str]]:
        """
        Consulta el Plan de Cuentas (/accounts) vía API v2.0
        y extrae únicamente las cuentas de nivel de detalle de BANCOS (1.01.01.02.01.XX).
        """
        cuentas_gl = []
        try:
            tenant_id = AZURE_CONFIG.get('tenant_id')
            env_prd = BC_ENV.get('prd', 'Production')
            
            url_accounts = f"{BC_API_BASE}/{tenant_id}/{env_prd}/api/v2.0/companies({company_id})/accounts"
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json"
            }

            res = requests.get(url_accounts, headers=headers, timeout=20)
            if res.ok:
                accounts_data = res.json().get("value", [])
                for acc in accounts_data:
                    acc_num = str(acc.get("number") or "").strip()
                    acc_name = str(acc.get("displayName") or acc.get("name") or "").strip()

                    # Filtrar cuentas contables del nivel exacto de BANCOS (1.01.01.02.01.XX)
                    if acc_num.startswith("1.01.01.02.01."):
                        cuentas_gl.append({
                            "account_number": acc_num,
                            "display_name": acc_name,
                            "digits_only": re.sub(r'[^\d]', '', acc_name)
                        })

        except Exception as e:
            print(f"⚠️ Advertencia al consultar el Plan de Cuentas (/accounts): {str(e)}")

        return cuentas_gl

    @classmethod
    def sincronizar_catalogo_cuentas_bc(cls) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Sincroniza el catálogo deduciendo la cuenta contable exacta
        cruzando el número de cuenta bancaria contra el Plan de Cuentas.
        """
        try:
            token, err_token = get_oauth_token()
            if err_token:
                return [], f"Fallo de autenticación en Azure AD: {err_token}"

            comp_info, err_company = resolve_company_info(token)
            if err_company:
                return [], f"Fallo al resolver la empresa en Business Central: {err_company}"

            company_id = comp_info[0] if isinstance(comp_info, (tuple, list)) else comp_info

            tenant_id = AZURE_CONFIG.get('tenant_id')
            env_prd = BC_ENV.get('prd', 'Production')
            base_url = f"{BC_API_BASE}/{tenant_id}/{env_prd}/api/v2.0/companies({company_id})"

            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json"
            }

            # 1. Obtener Grupos del Cubo y Plan de Cuentas (/accounts)
            mapa_grupos = cls._obtener_mapa_grupos_cubo(token)
            cuentas_gl_bancos = cls._obtener_cuentas_plan_contable(token, company_id)

            # 2. Consultar Maestro de Cuentas Bancarias (/bankAccounts)
            res_banks = requests.get(f"{base_url}/bankAccounts", headers=headers, timeout=20)
            if not res_banks.ok:
                return [], f"Error en API REST v2.0 bankAccounts ({res_banks.status_code}): {res_banks.text}"

            banks_data = res_banks.json().get("value", [])

            cuentas_sincronizadas = []
            for bank in banks_data:
                codigo_bc = str(bank.get("number") or "").strip()

                if not codigo_bc.upper().startswith("B"):
                    continue

                nombre_cta = str(bank.get("displayName") or bank.get("name") or "").strip()
                num_cta = str(bank.get("bankAccountNumber") or "").strip()
                grupo_bc = mapa_grupos.get(codigo_bc)

                # 3. MATCH EXACTO DE CUENTA CONTABLE
                num_cta_digits = re.sub(r'[^\d]', '', num_cta)
                cuenta_gl_encontrada = None

                # Coincidencia por dígitos del número de cuenta en la descripción del Plan de Cuentas
                if num_cta_digits and len(num_cta_digits) >= 4:
                    num_sin_ceros = num_cta_digits.lstrip('0')
                    for gl in cuentas_gl_bancos:
                        if num_cta_digits in gl["digits_only"] or (num_sin_ceros and num_sin_ceros in gl["digits_only"]):
                            cuenta_gl_encontrada = gl["account_number"]
                            break

                # Coincidencia por nombre / grupo si el banco no tiene número de cuenta grabado
                if not cuenta_gl_encontrada and grupo_bc:
                    grupo_clean = grupo_bc.upper().replace("BANCO", "").replace("BCO", "").strip()
                    for gl in cuentas_gl_bancos:
                        if grupo_clean and grupo_clean in gl["display_name"].upper():
                            cuenta_gl_encontrada = gl["account_number"]
                            break

                cuentas_sincronizadas.append({
                    "codigo_banco_bc": codigo_bc,
                    "nombre_cuenta": nombre_cta,
                    "numero_cuenta": num_cta if num_cta else None,
                    "grupo_registro_bc": grupo_bc,
                    "cuenta_contable_bc": cuenta_gl_encontrada,
                    "moneda": str(bank.get("currencyCode") or "USD").strip()
                })

            return cuentas_sincronizadas, None

        except Exception as e:
            return [], f"Excepción en sincronización de Bancos: {str(e)}"