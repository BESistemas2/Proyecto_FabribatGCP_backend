# app/core/bc_auth.py
import time
import requests
from .config import AZURE_CONFIG, MS_LOGIN_BASE, BC_CONFIG

# --- VARIABLES GLOBALES DE CACHÉ EN MEMORIA ---
_CACHED_TOKEN = None
_TOKEN_EXPIRY = 0

def get_oauth_token():
    """
    Obtiene el token de acceso OAuth 2.0 desde Microsoft Azure usando Client Credentials.
    Mantiene un mecanismo de caché en memoria RAM para evitar peticiones redundantes a Azure.
    """
    global _CACHED_TOKEN, _TOKEN_EXPIRY
    
    # Si el token existe en caché y le quedan más de 60 segundos de vida, lo reutilizamos
    if _CACHED_TOKEN and time.time() < (_TOKEN_EXPIRY - 60):
        return _CACHED_TOKEN, None
        
    if not AZURE_CONFIG.get('tenant_id'):
        return None, "Falta configurar BC_S_AZURE_TENANT_ID en las variables de entorno."
    
    token_url = f"{MS_LOGIN_BASE}/{AZURE_CONFIG['tenant_id']}/oauth2/v2.0/token"
    payload = {
        'grant_type': 'client_credentials',
        'client_id': AZURE_CONFIG['client_id'],
        'client_secret': AZURE_CONFIG['client_secret'],
        'scope': AZURE_CONFIG['scope']
    }
    
    try:
        response = requests.post(token_url, data=payload, timeout=15)
        if not response.ok:
            return None, f"Azure Error {response.status_code}: {response.text}"
            
        data = response.json()
        _CACHED_TOKEN = data.get('access_token')
        _TOKEN_EXPIRY = time.time() + int(data.get('expires_in', 3599))
        return _CACHED_TOKEN, None
    except Exception as e:
        return None, f"Excepción al conectar con Azure: {str(e)}"

def resolve_company_info(token):
    """
    Resuelve y retorna una tupla (company_id, company_name) requerida para las peticiones
    a las APIs v2.0 y ODataV4 de Dynamics 365 Business Central.
    """
    if BC_CONFIG.get('company_id') and BC_CONFIG.get('company_name'):
         return (BC_CONFIG['company_id'], BC_CONFIG['company_name']), None

    url = f"{BC_CONFIG['base_url_prd']}/companies"
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/json'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        if not response.ok:
            return None, f"BC Error {response.status_code}: {response.text}"
            
        companies = response.json().get('value', [])
        target = BC_CONFIG.get('company_name')
        
        if target:
            for comp in companies:
                comp_name = comp.get('name', '')
                display_name = comp.get('displayName', '')
                if comp_name.lower() == target.lower() or display_name.lower() == target.lower():
                    return (comp.get('id'), comp.get('name')), None
        
        if companies:
            return (companies[0].get('id'), companies[0].get('name')), None
            
        return None, "No se encontraron empresas disponibles en este Tenant de Business Central."
    except Exception as e:
        return None, f"Excepción al resolver información de la empresa: {str(e)}"