import logging

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core import config
from app.modulos.bancos.router import router as bancos_router
from app.modulos.conciliacion.router import router as conciliacion_router
from app.modulos.identidad.router import router as identidad_router

# from app.modulos.cobranzas.router import router as cobranzas_router  # pendiente de migrar a FastAPI

logging.basicConfig(
    level=config.LOG_LEVEL.upper(),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("fabribat")

app = FastAPI(
    title="Fabribat Middleware API",
    description="Ingesta bancaria, ERP Dynamics 365 BC y conciliación contable",
    version="2.1.0",
    docs_url="/docs" if config.ENABLE_DOCS else None,
    redoc_url="/redoc" if config.ENABLE_DOCS else None,
    openapi_url="/openapi.json" if config.ENABLE_DOCS else None,
)

# CORS: orígenes permitidos desde la variable CORS_ORIGINS (separados por coma)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    expose_headers=["X-Total-Count", "Content-Disposition"],
)


@app.exception_handler(Exception)
async def error_no_controlado(request: Request, exc: Exception):
    """Cualquier error no previsto: detalle al log, mensaje genérico al cliente."""
    logger.exception("Error no controlado en %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Error interno del servidor."})


app.include_router(bancos_router)
app.include_router(conciliacion_router)
app.include_router(identidad_router)


@app.get("/health", tags=["Health Check"])
def health_check():
    """Liveness para Docker / Portainer."""
    return {"status": "online", "version": app.version}


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=config.APP_ENV == "development")
