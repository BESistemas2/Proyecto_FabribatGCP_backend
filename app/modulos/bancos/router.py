# app/modulos/bancos/router.py
import uuid
from flask import Blueprint, request, jsonify
from .service import procesar_archivo_bancos_service

bancos_bp = Blueprint('bancos_v1', __name__, url_prefix='/api/v1/bancos')

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
            # 422 Unprocessable Entity: El archivo se subió, pero no cumple las reglas de negocio (ej. duplicado o mal formato)
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