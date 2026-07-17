# app/modulos/bancos/router.py
import os
import requests
import uuid
import logging
from flask import Blueprint, request, jsonify
from .service import procesar_archivo_bancos_service

# Configuramos un logger para ver qué nos envía Telegram en la consola de GCP
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bancos_bp = Blueprint('bancos_v1', __name__, url_prefix='/api/v1/bancos')

# ==========================================
# ENDPOINT 1: CARGA DE ARCHIVOS BANCARIOS
# ==========================================
@bancos_bp.route('/cargas', methods=['POST'])
def cargar_archivo_bancario():
    """
    POST /api/v1/bancos/cargas
    Endpoint que recibe un archivo CSV o Excel de un banco (Pichincha, Guayaquil, Produbanco)
    a través de multipart/form-data y ejecuta el motor ETL de Pandas para procesarlo.
    """
    # 1. Validar que la petición contiene un archivo
    if 'archivo' not in request.files:
        return jsonify({"error": "No se encontró el campo 'archivo' en la petición."}), 400
        
    archivo = request.files['archivo']
    
    if archivo.filename == '':
        return jsonify({"error": "No se seleccionó ningún archivo para subir."}), 400

    # 2. Extraer metadatos obligatorios enviados en el formulario (multipart)
    id_institucion = request.form.get('idInstitucion')
    usuario_carga = request.form.get('usuario')
    
    if not id_institucion or not usuario_carga:
        return jsonify({"error": "Los campos 'idInstitucion' y 'usuario' son obligatorios."}), 400

    try:
        # Leemos el archivo en memoria (en bytes)
        file_data = archivo.read()
        
        # Generamos un ID único para la auditoría de esta carga
        id_carga = str(uuid.uuid4())
        
        # 3. Delegamos el procesamiento pesado al Servicio (Motor ETL)
        exito, mensaje = procesar_archivo_bancos_service(
            file_data=file_data, 
            id_carga=id_carga, 
            id_institucion=id_institucion, 
            filename_original=archivo.filename, 
            usuario_carga=usuario_carga
        )

        if not exito:
            # 422 Unprocessable Entity: El archivo se subió, pero no cumple las reglas de negocio
            return jsonify({
                "status": "failed",
                "idCarga": id_carga,
                "error": mensaje
            }), 422
            
        return jsonify({
            "status": "success",
            "idCarga": id_carga,
            "mensaje": mensaje
        }), 201

    except Exception as e:
        return jsonify({"error": f"Fallo inesperado en el enrutador de bancos: {str(e)}"}), 500

import os
import requests
# ... (deja tus otros imports intactos arriba, como uuid, logging, Blueprint, etc.)

# Obtenemos el token de las variables de entorno de Cloud Run
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

def enviar_mensaje_telegram(chat_id, texto):
    """Función auxiliar para enviar mensajes de vuelta al usuario vía Telegram API"""
    if not TELEGRAM_TOKEN:
        logger.error("Falta configurar TELEGRAM_TOKEN en las variables de entorno.")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": texto
    }
    try:
        respuesta = requests.post(url, json=payload)
        respuesta.raise_for_status() # Verifica si hubo errores de red (Ej. 404, 401)
    except Exception as e:
        logger.error(f"❌ Error al enviar mensaje a Telegram: {e}")

# ==========================================
# ENDPOINT 2: WEBHOOK DE TELEGRAM
# ==========================================
@bancos_bp.route('/webhook', methods=['POST'])
def telegram_webhook():
    try:
        data = request.get_json()
        
        if data and "message" in data:
            chat_id = data["message"]["chat"]["id"]
            texto = data["message"].get("text", "").lower()
            
            logger.info(f"Usuario {chat_id} escribió: {texto}")
            
            # --- LÓGICA DE RESPUESTA BÁSICA ---
            if texto in ['/start', 'hola']:
                respuesta = (
                    f"¡Hola! 👋 Conexión exitosa al backend de Fabribat.\n\n"
                    f"Tu ID de usuario es: {chat_id}\n\n"
                    f"Por favor, envíame tu **Ubicación** actual usando el clip (📎) de Telegram para validar la geocerca."
                )
                enviar_mensaje_telegram(chat_id, respuesta)
            else:
                enviar_mensaje_telegram(chat_id, "Recibí tu mensaje, pero por ahora solo entiendo 'hola' o '/start'.")
            
        return jsonify({"ok": True}), 200
        
    except Exception as e:
        logger.error(f"❌ Error procesando el webhook: {e}")
        return jsonify({"ok": True, "error": str(e)}), 200