from flask import Blueprint

identidad_bp = Blueprint('identidad', __name__, url_prefix='/api/identidad')

@identidad_bp.route('/health', methods=['GET'])
def health():
    return {"status": "ok", "modulo": "identidad"}