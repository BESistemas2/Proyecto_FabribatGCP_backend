# app/modulos/bancos/router.py
import os
import requests
import uuid
import logging
import math
from flask import Blueprint, request, jsonify
from .service import procesar_archivo_bancos_service

# Configuramos un logger para ver qué nos envía Telegram en la consola de GCP
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bancos_bp = Blueprint('bancos_v1', __name__, url_prefix='/api/v1/bancos')

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Coordenadas de prueba (Ejemplo: Oficina central en Quito, Ecuador)
# Reemplaza estas coordenadas por las de tu sucursal real de pruebas
SUCURSAL_LAT = -0.1807
SUCURSAL_LON = -78.4678
RANGO_PERMITIDO_METROS = 200.0  # Geocerca de 200 metros alrededor de la sucursal


def calcular_distancia(lat1, lon1, lat2, lon2):
    """
    Calcula la distancia en metros entre dos coordenadas geográficas
    utilizando la fórmula de Haversine.
    """
    # Radio de la Tierra en metros
    R = 6371000.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)

    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = math.sin(delta_phi / 2.0) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * \
        math.sin(delta_lambda / 2.0) ** 2

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    # Retorna la distancia en metros
    return R * c


def enviar_mensaje_telegram(chat_id, texto):
    """Envía un mensaje de texto de vuelta al chat del usuario"""
    if not TELEGRAM_TOKEN:
        logger.error("Falta configurar TELEGRAM_TOKEN")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": "Markdown"  # Permite usar negritas (*texto*) en las respuestas
    }
    try:
        respuesta = requests.post(url, json=payload)
        respuesta.raise_for_status()
    except Exception as e:
        logger.error(f"❌ Error al enviar mensaje: {e}")

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



# ==========================================
# ENDPOINT 2: WEBHOOK DE TELEGRAM
# ==========================================
@bancos_bp.route('/webhook', methods=['POST'])
def telegram_webhook():
    try:
        data = request.get_json()
        
        if data and "message" in data:
            chat_id = data["message"]["chat"]["id"]
            
            # Case A: El usuario envió su ubicación
            if "location" in data["message"]:
                user_lat = data["message"]["location"]["latitude"]
                user_lon = data["message"]["location"]["longitude"]
                
                # Calculamos la distancia real entre el usuario y la sucursal
                distancia = calcular_distancia(user_lat, user_lon, SUCURSAL_LAT, SUCURSAL_LON)
                logger.info(f"📍 Ubicación recibida de {chat_id}: Lat={user_lat}, Lon={user_lon}. Distancia={distancia:.2f}m")
                
                if distancia <= RANGO_PERMITIDO_METROS:
                    respuesta = (
                        f"✅ *Ubicación verificada con éxito.*\n"
                        f"Te encuentras a {distancia:.1f} metros de la sucursal asignada.\n\n"
                        f"🔐 *Paso final:* Por favor, escribe el código de seguridad de 6 dígitos de tu Google Authenticator."
                    )
                else:
                    respuesta = (
                        f"❌ *Acceso denegado por Geocerca.*\n"
                        f"Tu ubicación actual está a {distancia:.1f} metros de la sucursal asignada.\n"
                        f"Debes estar a menos de {RANGO_PERMITIDO_METROS} metros de tu lugar de trabajo para procesar cobros."
                    )
                enviar_mensaje_telegram(chat_id, respuesta)
                
            # Case B: El usuario envió un mensaje de texto normal
            elif "text" in data["message"]:
                texto = data["message"].get("text", "").lower()
                logger.info(f"💬 Usuario {chat_id} escribió: {texto}")
                
                if texto in ['/start', 'hola']:
                    respuesta = (
                        f"¡Hola! 👋 Bienvenido al bot de operaciones de Fabribat.\n\n"
                        f"Tu ID de usuario es: `{chat_id}`\n\n"
                        f"📍 Para continuar, presiona el botón de adjuntar (clip 📎) y envíame tu **Ubicación** actual."
                    )
                    enviar_mensaje_telegram(chat_id, respuesta)
                else:
                    enviar_mensaje_telegram(chat_id, "Usa `/start` para iniciar el proceso o envíame tu ubicación.")
            
        return jsonify({"ok": True}), 200
        
    except Exception as e:
        logger.error(f"❌ Error en webhook: {e}")
        return jsonify({"ok": True, "error": str(e)}), 200