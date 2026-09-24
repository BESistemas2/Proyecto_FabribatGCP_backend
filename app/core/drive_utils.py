# app/core/drive_utils.py
import re
import requests
from typing import Tuple


def extraer_drive_file_id(input_str: str) -> str:
    """
    Extrae el ID único del archivo desde una URL de Google Drive, 
    un ID directo o una ruta de AppSheet.
    """
    input_str = (input_str or "").strip()
    
    # 1. Patrón tipo /file/d/FILE_ID/view
    match_d = re.search(r'/file/d/([a-zA-Z0-9_-]+)', input_str)
    if match_d:
        return match_d.group(1)
        
    # 2. Patrón tipo uc?id=FILE_ID
    match_id = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', input_str)
    if match_id:
        return match_id.group(1)
        
    # 3. ID directo alfanumérico largo
    if re.match(r'^[a-zA-Z0-9_-]{25,50}$', input_str):
        return input_str

    return ""


def descargar_desde_google_drive(file_id: str) -> Tuple[bytes, str]:
    """
    Descarga el contenido binario del archivo público/compartido desde Google Drive.
    """
    url = "https://drive.google.com/uc?export=download"
    session = requests.Session()
    
    print(f"📥 [Drive] Descargando archivo con File ID: '{file_id}'...")
    response = session.get(url, params={'id': file_id}, stream=True, timeout=40)
    
    # Manejo de token de confirmación para archivos de mayor tamaño
    for key, value in response.cookies.items():
        if key.startswith('download_warning'):
            response = session.get(url, params={'id': file_id, 'confirm': value}, stream=True, timeout=40)
            break
            
    if not response.ok:
        raise ValueError(f"No se pudo descargar el archivo de Google Drive (Status HTTP {response.status_code})")
        
    # Extraer nombre del archivo si viene en las cabeceras
    filename = f"extracto_drive_{file_id}.xlsx"
    cd = response.headers.get('content-disposition')
    if cd:
        filenames = re.findall('filename="?([^"]+)"?', cd)
        if filenames:
            filename = filenames[0]

    return response.content, filename