# app/modulos/bancos/router.py
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


# ==========================================
# ENDPOINT 2: WEBHOOK DE TELEGRAM
# ==========================================
@bancos_bp.route('/webhook', methods=['POST'])
def telegram_webhook():
    """
    POST /api/v1/bancos/webhook
    Este es el endpoint que Telegram golpeará cada vez que alguien escriba al bot.
    """
    try:
        # 1. Capturamos el JSON que nos envía Telegram
        data = request.get_json()
        logger.info(f"📩 Nuevo mensaje de Telegram: {data}")
        
        # 2. Verificamos que sea un mensaje de texto normal
        if data and "message" in data:
            chat_id = data["message"]["chat"]["id"]
            texto = data["message"].get("text", "")
            
            logger.info(f"Usuario {chat_id} escribió: {texto}")
            
            # TODO: Aquí agregaremos la validación en base de datos (fabribat_cobranzas_test)
            # TODO: Aquí validaremos el OTP y la Geocerca
            # TODO: Aquí enviaremos la respuesta de vuelta a Telegram usando el TOKEN
            
        # 3. ¡Súper Importante! Siempre debemos responderle a Telegram que todo salió OK.
        return jsonify({"ok": True}), 200
        
    except Exception as e:
        logger.error(f"❌ Error procesando el webhook: {e}")
        # Retornamos 200 OK incluso si falla nuestro código para evitar un bucle de Telegram
        return jsonify({"ok": True, "error": str(e)}), 200