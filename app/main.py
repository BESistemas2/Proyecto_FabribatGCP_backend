import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Importación de los ruteadores por dominio funcional
from app.modulos.bancos.router import router as bancos_router
from app.modulos.conciliacion.router import router as conciliacion_router

# Descomentar a medida que completes el traspaso a FastAPI:
# from app.modulos.cobranzas.router import router as cobranzas_router
# from app.modulos.identidad.router import router as identidad_router

app = FastAPI(
    title="Fabribat Middleware API",
    description="Arquitectura Modular para Ingesta Bancaria, ERP Dynamics 365 BC y Conciliación Contable",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Configuración de CORS para permitir peticiones del Frontend / AppSheet / Zoho
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registro de routers por dominio funcional (Reemplaza a los Blueprints de Flask)
app.include_router(bancos_router)
app.include_router(conciliacion_router)
# app.include_router(cobranzas_router)
# app.include_router(identidad_router)


@app.get("/health", tags=["Health Check"])
def health_check():
    """Health check global del Middleware para monitoreo en GCP Cloud Run."""
    return {
        "status": "online",
        "empresa": "Fabribat",
        "middleware": "FastAPI Modular REST API",
        "engine": "PostgreSQL (fabribat_db)"
    }


if __name__ == "__main__":
    # Ejecución local en el puerto 8080 (Mismo puerto expuesto para GCP Cloud Run)
    uvicorn.run("app.main:app", host="0.0.0.0", port=8080, reload=True)