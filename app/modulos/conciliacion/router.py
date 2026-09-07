# app/modulos/conciliacion/router.py
from flask import Blueprint, request, jsonify
from app.modulos.conciliacion.service import ConciliacionService

conciliacion_bp = Blueprint('conciliacion', __name__, url_prefix='/api/conciliacion')

@conciliacion_bp.route('/mayor-editado', methods=['GET'])
def obtener_mayor_editado():
    """
    Endpoint para consultar el Libro Mayor procesado de un periodo.
    Uso: /api/conciliacion/mayor-editado?fecha_inicio=2026-05-01&fecha_fin=2026-05-31
    """
    fecha_inicio = request.args.get('fecha_inicio')
    fecha_fin = request.args.get('fecha_fin')

    if not fecha_inicio or not fecha_fin:
        return jsonify({"status": "error", "message": "Parámetros 'fecha_inicio' y 'fecha_fin' son obligatorios"}), 400

    df_mayor, error = ConciliacionService.obtener_y_preparar_mayor(fecha_inicio, fecha_fin)
    if error:
        return jsonify({"status": "error", "message": error}), 500

    return jsonify({
        "status": "success",
        "total_registros": len(df_mayor),
        "data": df_mayor.to_dict(orient='records')
    }), 200