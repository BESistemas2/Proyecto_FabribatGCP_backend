# app/modulos/cobranzas/router.py
from flask import Blueprint, request, jsonify
from .service import CobranzasService 
from .bc_service import BusinessCentralSyncService

cobranzas_bp = Blueprint('cobranzas_v1', __name__, url_prefix='/api/v1/cobranzas')

# =========================================================================
# 1. RECURSO: FACTURAS PENDIENTES
# =========================================================================
@cobranzas_bp.route('/facturas', methods=['GET'])
def listar_facturas_pendientes():
    """
    GET /api/v1/cobranzas/facturas
    Retorna el catálogo de facturas con saldo pendiente para AppSheet.
    Soporta filtrado opcional por idCliente o ruc vía Query Params.
    """
    id_cliente = request.args.get('idCliente')
    ruc = request.args.get('ruc')
    
    try:
        # Consumimos la lógica del servicio ORM[cite: 1]
        facturas = CobranzasService.obtener_facturas_pendientes(id_cliente, ruc)
        return jsonify({
            "status": "success",
            "count": len(facturas),
            "data": facturas
        }), 200
    except Exception as e:
        return jsonify({"error": f"Error al recuperar facturas: {str(e)}"}), 500


# =========================================================================
# 2. RECURSO: COMPROBANTES DE PAGO (RECIBOS DE COBRO)
# =========================================================================

@cobranzas_bp.route('/comprobantes', methods=['POST'])
def registrar_comprobante_cobro():
    datos_entrada = request.get_json()
    if not datos_entrada:
        return jsonify({"error": "El cuerpo de la petición no puede estar vacío."}), 400
        
    # Invocamos al servicio transaccional
    resultado, error = CobranzasService.procesar_comprobante_completo(datos_entrada)
    
    if error:
        return jsonify({"error": error}),422  # 422 Unprocessable Entity (Error de lógica de negocio)
        
    return jsonify({
        "status": "success",
        "data": resultado
    }), 201


@cobranzas_bp.route('/comprobantes/<string:id_comprobante>', methods=['GET'])
def obtener_detalle_comprobante(id_comprobante):
    """
    GET /api/v1/cobranzas/comprobantes/<id>
    Retorna la cabecera de un recibo específico junto con sus formas de pago 
    y el detalle de las facturas liquidadas.
    """
    try:
        # TODO: Invocar servicio de búsqueda por ID
        return jsonify({
            "idComprobante": id_comprobante,
            "mensaje": "Detalle del comprobante recuperado (Mock)"
        }), 200
    except Exception as e:
        return jsonify({"error": f"Error al buscar el comprobante: {str(e)}"}), 500


# =========================================================================
# 3. ACCIONES COMPLEMENTARIAS: INTEGRACIÓN ERP & CIERRES
# =========================================================================
@cobranzas_bp.route('/comprobantes/<string:id_comprobante>/sincronizacion', methods=['POST'])
def forzar_sincronizacion_erp(id_comprobante):
    """
    POST /api/v1/cobranzas/comprobantes/<id>/sincronizacion
    Dispara la inyección en tiempo real del recibo al lote contable de Dynamics 365 BC.
    """
    try:
        # Invocamos al servicio de infraestructura que acabamos de crear
        exito, mensaje = BusinessCentralSyncService.sincronizar_comprobante_journal(id_comprobante)
        
        if not exito:
            return jsonify({
                "status": "failed",
                "error": mensaje
            }), 422
            
        return jsonify({
            "status": "success",
            "mensaje": mensaje
        }), 200
        
    except Exception as e:
        return jsonify({"error": f"Fallo inesperado en el enrutador de sincronización: {str(e)}"}), 500


@cobranzas_bp.route('/cierres-caja', methods=['POST'])
def generar_cierre_caja():
    """
    POST /api/v1/cobranzas/cierres-caja
    Consolida la recaudación actual del cajero.
    Espera en el payload: { "idUsuario": "usuario@fabribat.com", "caja": "CAJA01", "observaciones": "Turno mañana" }
    """
    datos_cierre = request.get_json()
    if not datos_cierre:
        return jsonify({"error": "El cuerpo de la petición no puede estar vacío."}), 400

    id_usuario = datos_cierre.get("idUsuario")
    caja_codigo = datos_cierre.get("caja")
    observaciones = datos_cierre.get("observaciones")

    if not id_usuario or not caja_codigo:
        return jsonify({"error": "Los parámetros 'idUsuario' y 'caja' son obligatorios."}), 400

    try:
        # Invocamos al servicio de base de datos
        resultado, error = CobranzasService.generar_cierre_caja(id_usuario, caja_codigo, observaciones)
        
        if error:
            return jsonify({"error": error}), 422
            
        return jsonify({
            "status": "success",
            "data": resultado
        }), 201

    except Exception as e:
        return jsonify({"error": f"Fallo inesperado al ejecutar el enrutador de cierres: {str(e)}"}), 500