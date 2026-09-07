# app/modulos/conciliacion/bc_service.py
import requests
from typing import List, Dict, Any, Tuple
from app.core.config import BC_CONFIG
from app.core.bc_auth import get_oauth_token, resolve_company_info

class BusinessCentralReaderService:
    @staticmethod
    def consultar_mayor_bancos(fecha_inicio: str, fecha_fin: str) -> Tuple[List[Dict[str, Any]], str]:
        """
        Realiza una consulta GET de solo lectura al endpoint 'generalLedgerEntries'
        filtrando estrictamente por rango de fechas y por las 6 cuentas bancarias autorizadas.
        """
        try:
            # Reutiliza tu lógica nativa de autenticación en Azure AD
            token, err_token = get_oauth_token()
            if err_token:
                return [], f"Fallo de autenticación en Azure: {err_token}"

            comp_info, err_company = resolve_company_info(token)
            if err_company:
                return [], f"Fallo al resolver la empresa en BC: {err_company}"
            company_id, _ = comp_info

            # Catálogo oficial de las 6 cuentas contables de bancos de FABRIBAT [9]
            cuentas_bancos = [
                "1.01.01.02.01.01",  # PRODUBANCO
                "1.01.01.02.01.02",  # PICHINCHA
                "1.01.01.02.01.03",  # SOLIDARIO
                "1.01.01.02.01.05",  # GUAYAQUIL
                "1.01.01.02.01.09",  # PICHINCHA PUNTOS
                "1.01.01.02.01.12"   # GUAYAQUIL INV
            ]

            # Construcción del filtro OData
            cuentas_filter = " or ".join([f"gLAccountNo eq '{cta}'" for cta in cuentas_bancos])
            odata_filter = f"postingDate ge {fecha_inicio} and postingDate le {fecha_fin} and ({cuentas_filter})"
            
            # Selección de campos indispensables para el análisis
            fields_select = "postingDate,documentNo,description,externalDocumentNo,debitAmount,creditAmount,gLAccountNo"
            
            base_url = f"https://api.businesscentral.dynamics.com/v2.0/{BC_CONFIG['tenant_id']}/Production/api/v2.0"
            endpoint_url = f"{base_url}/companies({company_id})/generalLedgerEntries?$filter={odata_filter}&$select={fields_select}"

            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }

            response = requests.get(endpoint_url, headers=headers, timeout=30)
            if not response.ok:
                return [], f"Error en API de Dynamics: {response.status_code} - {response.text}"

            return response.json().get("value", []), None

        except Exception as e:
            return [], f"Excepción al conectar con el ERP: {str(e)}"