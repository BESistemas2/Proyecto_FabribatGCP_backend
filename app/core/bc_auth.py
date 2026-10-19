# app/core/bc_auth.py
import logging
import threading
import time

import requests

from .config import AZURE_CONFIG, BC_CONFIG, MS_LOGIN_BASE

logger = logging.getLogger(__name__)

# Caché en memoria del token de Azure (compartida entre hilos del worker)
_CACHED_TOKEN = None
_TOKEN_EXPIRY = 0.0
_LOCK = threading.Lock()


def get_oauth_token():
    """
    Token OAuth 2.0 de Azure AD (client credentials) para Business Central.
    Devuelve (token, error). El error es un texto técnico para el log, no para el usuario.
    """
    global _CACHED_TOKEN, _TOKEN_EXPIRY
    with _LOCK:
        if _CACHED_TOKEN and time.time() < _TOKEN_EXPIRY - 60:
            return _CACHED_TOKEN, None

        token_url = f"{MS_LOGIN_BASE}/{AZURE_CONFIG['tenant_id']}/oauth2/v2.0/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": AZURE_CONFIG["client_id"],
            "client_secret": AZURE_CONFIG["client_secret"],
            "scope": AZURE_CONFIG["scope"],
        }
        try:
            response = requests.post(token_url, data=payload, timeout=15)
        except requests.RequestException as e:
            logger.error("No se pudo conectar con Azure AD: %s", e)
            return None, f"Excepción al conectar con Azure: {e}"

        if not response.ok:
            logger.error("Azure AD respondió %s: %s", response.status_code, response.text[:500])
            return None, f"Azure Error {response.status_code}"

        data = response.json()
        _CACHED_TOKEN = data.get("access_token")
        _TOKEN_EXPIRY = time.time() + int(data.get("expires_in", 3599))
        return _CACHED_TOKEN, None


def resolve_company_info(token):
    """
    Devuelve ((company_id, company_name), error) para las APIs v2.0 y ODataV4 de BC.
    Si BC_G_COMPANY_ID y BC_G_COMPANY_NAME están configurados no se consulta a BC.
    """
    if BC_CONFIG.get("company_id") and BC_CONFIG.get("company_name"):
        return (BC_CONFIG["company_id"], BC_CONFIG["company_name"]), None

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        response = requests.get(f"{BC_CONFIG['base_url_prd']}/companies", headers=headers, timeout=15)
    except requests.RequestException as e:
        logger.error("No se pudo consultar empresas en BC: %s", e)
        return None, f"Excepción al resolver la empresa: {e}"

    if not response.ok:
        logger.error("BC /companies respondió %s: %s", response.status_code, response.text[:500])
        return None, f"BC Error {response.status_code}"

    companies = response.json().get("value", [])
    target = (BC_CONFIG.get("company_name") or "").lower()
    for comp in companies:
        if target and target in (str(comp.get("name", "")).lower(), str(comp.get("displayName", "")).lower()):
            return (comp.get("id"), comp.get("name")), None
    if companies:
        return (companies[0].get("id"), companies[0].get("name")), None
    return None, "No se encontraron empresas en este tenant de Business Central."
