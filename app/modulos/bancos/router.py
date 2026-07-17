# app/modulos/bancos/router.py
import uuid
import logging
import os
import math
import requests
import threading
import collections
from flask import Blueprint, request, jsonify
from .service import procesar_archivo_bancos_service

# Configuramos el logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bancos_bp = Blueprint('bancos_v1', __name__, url_prefix='/api/v1/bancos')

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Coordenadas de la oficina de pruebas (Quito, Ecuador)
SUCURSAL_LAT = -0.1807
SUCURSAL_LON = -78.4678
RANGO_PERMITIDO_METROS = 200.0  # Geocerca máxima permitida

# Deduplicación de mensajes: Cola de tamaño fijo para recordar los últimos 1000 mensajes
PROCESSED_UPDATES = collections.deque(maxlen=1000)
processed_lock = threading.Lock()


def calcular_distancia(lat1, lon1, lat2, lon2):
    """Calcula la distancia en metros entre dos coordenadas con Haversine"""
    R = 6371000.0  # Radio de la Tierra en metros
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * \
        math.sin(delta_lambda / 2.0) ** 2

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def enviar_mensaje_telegram(chat_id, texto):
    """Envía una respuesta de vuelta al chat de Telegram"""
    if not TELEGRAM_TOKEN:
        logger.error("Falta configurar TELEGRAM_TOKEN")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "Markdown"
    }
    try:
        respuesta = requests.post(url, json=payload, timeout=10)
        respuesta.raise_for_status()
    except Exception as e:
        logger.error(f"❌ Error al enviar mensaje: {e}")


def procesar_mensaje_segundo_plano(data):
    """
    Función que corre en un hilo secundario de CPU.
    Aquí se hace el trabajo pesado sin bloquear a Telegram.
    """
    try:
        message = data.get("message", {})
        chat_id = message["chat"]["id"]
        
        # Case A: El usuario envió su ubicación
        if "location" in message:
            user_lat = message["location"]["latitude"]
            user_lon = message["location"]["longitude"]
            
            distancia = calcular_distancia(user_lat, user_lon, SUCURSAL_LAT, SUCURSAL_LON)
            logger.info(f"📍 [HILO] Ubicación recibida de {chat_id}: Lat={user_lat}, Lon={user_lon}. Distancia={distancia:.2f}m")
            
            if distancia <= RANGO_PERMITIDO_METROS:
                respuesta = (
                    f"✅ *Ubicación verificada con éxito.*\n"
                    f"Te encuentras a *{distancia:.1f} metros* de la sucursal asignada.\n\n"
                    f"🔐 *Paso final:* Por favor, escribe el código de seguridad de 6 dígitos de tu Google Authenticator."
                )
            else:
                respuesta = (
                    f"❌ *Acceso denegado por Geocerca.*\n"
                    f"Tu ubicación actual está a *{distancia:.1f} metros* de la sucursal asignada.\n"
                    f"Debes estar a menos de *{RANGO_PERMITIDO_METROS} metros* de tu lugar de trabajo para procesar cobros."
                )
            enviar_mensaje_telegram(chat_id, respuesta)
            
        # Case B: El usuario envió un texto
        elif "text" in message:
            texto = message.get("text", "").lower()
            logger.info(f"💬 [HILO] Usuario {chat_id} escribió: {texto}")
            
            if texto in ['/start', 'hola']:
                respuesta = (
                    f"¡Hola! 👋 Bienvenido al bot de operaciones de Fabribat.\n\n"
                    f"Tu ID de usuario es: `{chat_id}`\n\n"
                    f"📍 Para continuar, presiona el botón de adjuntar (clip 📎) y envíame tu **Ubicación** actual."
                )
                enviar_mensaje_telegram(chat_id, respuesta)
            else:
                enviar_mensaje_telegram(chat_id, "Usa `/start` para iniciar el proceso o envíame tu ubicación.")
                
    except Exception as e:
        logger.error(f"❌ Fallo en procesamiento asíncrono: {e}")


# ==========================================
# ENDPOINT 1: CARGA DE ARCHIVOS BANCARIOS
# ==========================================
@bancos_bp.route('/cargas', methods=['POST'])
def cargar_archivo_bancario():
    if 'archivo' not in request.files:
        return jsonify({"error": "No se encontró el campo 'archivo' en la petición."}), 400
    archivo = request.files['archivo']
    if archivo.filename == '':
        return jsonify({"error": "No se seleccionó ningún archivo para subir."}), 400
    id_institucion = request.form.get('idInstitucion')
    usuario_carga = request.form.get('usuario')
    if not id_institucion or not usuario_carga:
        return jsonify({"error": "Los campos 'idInstitucion' y 'usuario' son obligatorios."}), 400
    try:
        file_data = archivo.read()
        id_carga = str(uuid.uuid4())
        exito, mensaje = procesar_archivo_bancos_service(
            file_data=file_data, 
            id_carga=id_carga, 
            id_institucion=id_institucion, 
            filename_original=archivo.filename, 
            usuario_carga=usuario_carga
        )
        if not exito:
            return jsonify({"status": "failed", "idCarga": id_carga, "error": mensaje}), 422
        return jsonify({"status": "success", "idCarga": id_carga, "mensaje": mensaje}), 201
    except Exception as e:
        return jsonify({"error": f"Fallo inesperado: {str(e)}"}), 500


# ==========================================
# ENDPOINT 2: WEBHOOK DE TELEGRAM (OPTIMIZADO)
# ==========================================
@bancos_bp.route('/webhook', methods=['POST'])
def telegram_webhook():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"ok": True}), 200

        update_id = data.get("update_id")
        
        # 1. Deduplicación por seguridad
        if update_id:
            with processed_lock:
                if update_id in PROCESSED_UPDATES:
                    logger.info(f"♻️ Reintento de Telegram detectado (Update {update_id}). Descartando duplicado.")
                    return jsonify({"ok": True}), 200  # Respondemos OK rápido para frenar el spam
                PROCESSED_UPDATES.append(update_id)

        # 2. Responder INMEDIATAMENTE a Telegram para evitar retries (Spam)
        # Iniciamos el procesamiento real en un hilo secundario y nos desconectamos de inmediato de la red
        threading.Thread(target=procesar_mensaje_segundo_plano, args=(data,)).start()

        return jsonify({"ok": True}), 200

    except Exception as e:
        logger.error(f"❌ Error en la recepción del webhook: {e}")
        return jsonify({"ok": True, "error": str(e)}), 200