import requests
from typing import List, Dict, Any, Tuple, Optional
from urllib.parse import quote
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import AZURE_CONFIG, BC_ENV, BC_CONFIG
from app.core.bc_auth import get_oauth_token


class BusinessCentralReaderService:

    @classmethod
    def resolver_grupo_banco_por_cuenta(
        cls, 
        db: Session, 
        cuenta_o_grupo: str
    ) -> Tuple[Optional[str], Optional[str]]:
        try:
            sql = text("""
                SELECT grupo_registro_bc, codigo_banco_bc 
                FROM bancos.cuentas_bancarias 
                WHERE cuenta_contable_bc = :param 
                   OR codigo_banco_bc = :param 
                   OR grupo_registro_bc = :param
                LIMIT 1;
            """)
            res = db.execute(sql, {"param": cuenta_o_grupo.strip()}).fetchone()
            if res:
                return res.grupo_registro_bc, res.codigo_banco_bc
        except Exception as e:
            print(f"⚠️ Error resolviendo cuenta en BD: {str(e)}")
        
        return None, None

    @classmethod
    def consultar_mayor_bancos(
        cls, 
        fecha_inicio: str, 
        fecha_fin: str, 
        cuenta_contable: Optional[str] = None,
        grupo_registro_bc: Optional[str] = None,
        db: Optional[Session] = None
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        try:
            token, err_token = get_oauth_token()
            if err_token:
                return [], f"Fallo de autenticación en Azure AD: {err_token}"

            company_name = BC_CONFIG.get('company_name', 'FABRIBAT')
            company_param = quote(company_name)

            target_grupo = grupo_registro_bc
            target_codigo = None

            if cuenta_contable and db and not target_grupo:
                target_grupo, target_codigo = cls.resolver_grupo_banco_por_cuenta(db, cuenta_contable)

            if target_codigo:
                filtro_bancos = f"BankAccountNo eq '{target_codigo}'"
            elif target_grupo:
                filtro_bancos = f"BankAccPostingGroup eq '{target_grupo}'"
            elif cuenta_contable:
                filtro_bancos = f"BankAccPostingGroup eq '{cuenta_contable}'"
            else:
                filtro_bancos = "BankAccPostingGroup ne ''"

            odata_filter = f"PostingDate ge {fecha_inicio} and PostingDate le {fecha_fin} and {filtro_bancos}"
            
            # 1. Definir los mismos campos que usaste en Postman
            select_fields = "EntryNo,BankAccountNo,BankAccPostingGroup,PostingDate,DocumentNo,ExternalDocumentNo,Description,DebitAmount,CreditAmount,Amount"

            tenant_id = AZURE_CONFIG.get('tenant_id')
            env_prd = BC_ENV.get('prd', 'Production')
            odata_base = f"https://api.businesscentral.dynamics.com/v2.0/{tenant_id}/{env_prd}/ODataV4"
            
            # 2. Agregar &$select= a la URL final
            endpoint_url = (
                f"{odata_base}/Company('{company_param}')/GmasBankAccountLedgerEntr_Cubo"
                f"?$filter={odata_filter}&$select={select_fields}"
            )

            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json"
            }

            response = requests.get(endpoint_url, headers=headers, timeout=30)
            
            if not response.ok:
                return [], f"Error OData V4 Dynamics 365 BC ({response.status_code}): {response.text}"

            raw_entries = response.json().get("value", [])

            normalized_entries = []
            for item in raw_entries:
                doc_no = str(item.get("DocumentNo", "") or "").strip()
                ext_doc_no = str(item.get("ExternalDocumentNo", "") or "").strip()
                ref_final = ext_doc_no if ext_doc_no else doc_no

                normalized_entries.append({
                    "entry_no_bc": item.get("EntryNo"),
                    "codigo_banco_bc": item.get("BankAccountNo", ""),
                    "grupo_registro_bc": item.get("BankAccPostingGroup", ""),
                    "posting_date": str(item.get("PostingDate", "") or "").split("T")[0],
                    "document_no": doc_no,
                    "external_document_no": ref_final,
                    "description": item.get("Description", ""),
                    "debit_amount": float(item.get("DebitAmount", 0) or 0),
                    "credit_amount": float(item.get("CreditAmount", 0) or 0),
                    "monto": float(item.get("Amount", 0) or 0),
                    "gl_account_no": cuenta_contable or item.get("BankAccPostingGroup", "")
                })

            return normalized_entries, None

        # 3. Captura explícita de timeout para degradación elegante
        except requests.exceptions.Timeout:
            print("⚠️ Timeout de 30s en OData BC. Respondiendo con datos locales de PostgreSQL.")
            return [], "Timeout consultando Microsoft Dynamics 365 BC"

        except Exception as e:
            return [], f"Excepción al conectar con OData V4 BC: {str(e)}"