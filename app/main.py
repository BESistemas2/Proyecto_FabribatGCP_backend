# app/main.py
from flask import Flask, jsonify
from app.modulos.bancos.router import bancos_bp
from app.modulos.cobranzas.router import cobranzas_bp
from app.modulos.identidad.router import identidad_bp

def create_app():
    """
    Fábrica de la aplicación Fabribat.
    Se encarga de inicializar Flask y registrar los módulos del ecosistema.
    """
    app = Flask(__name__)
    
    # Registrar los Blueprints de cada dominio de negocio

    app.register_blueprint(bancos_bp)
    app.register_blueprint(cobranzas_bp)
    app.register_blueprint(identidad_bp)
    
    # Health check global del Middleware
    @app.route('/health', methods=['GET'])
    def health_check():
        return jsonify({
            "status": "online", 
            "empresa": "Fabribat", 
            "middleware": "Modular REST API"
        }), 200
        
    return app

# Instancia global para Cloud Run / Gunicorn
app = create_app()

if __name__ == '__main__':
    # Ejecución local en el puerto 8080
    app.run(host='0.0.0.0', port=8080, debug=True)