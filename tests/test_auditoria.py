"""Auditoría: utilidades, middleware de contexto y contrato del catálogo de acciones (sin base de datos)."""
import re
import threading
from contextvars import copy_context
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core import contexto
from app.modulos.auditoria.mantenimiento import migraciones_en_disco, migraciones_faltantes
from app.modulos.auditoria.middleware import MiddlewareContexto
from app.modulos.auditoria.servicio import diferencias, ocultar_sensibles

RAIZ = Path(__file__).resolve().parents[1]
MODULOS = ("identidad", "bancos", "conciliacion", "cobranzas", "sistema", "auditoria")


def test_diferencias_solo_campos_que_cambian():
    antes = {"rol": "BASICO", "nombre": "Ana", "monto": Decimal("10.0"), "creado": 1}
    despues = {"rol": "ADMIN", "nombre": "Ana", "monto": Decimal("10.00"), "creado": 2}
    assert diferencias(antes, despues, omitir=("creado",)) == {"rol": ["BASICO", "ADMIN"]}


def test_diferencias_campos_nuevos_y_listas_sin_orden():
    assert diferencias({}, {"a": 1}) == {"a": [None, 1]}
    assert diferencias({"d": ["b", "a"]}, {"d": ["a", "b"]}) == {}


def test_ocultar_sensibles_recursivo():
    datos = {"usuario": "x", "password": "123", "extra": {"api_key": "k", "lista": [{"token": "t", "ok": 1}]}}
    assert ocultar_sensibles(datos) == {"usuario": "x", "password": "***",
                                        "extra": {"api_key": "***", "lista": [{"token": "***", "ok": 1}]}}


def test_migraciones_faltantes():
    en_disco = migraciones_en_disco(RAIZ / "migrations")
    assert en_disco[11] == "011_auditoria.sql"
    assert migraciones_faltantes(en_disco, set(en_disco)) == []
    assert migraciones_faltantes({1: "001_a.sql", 2: "002_b.sql"}, {1}) == ["002_b.sql"]


def test_cada_migracion_desde_la_011_se_registra():
    for numero, nombre in migraciones_en_disco(RAIZ / "migrations").items():
        if numero >= 11:
            sql = (RAIZ / "migrations" / nombre).read_text(encoding="utf-8")
            assert re.search(rf"INSERT INTO public\.schema_migrations[^;]*\({numero},", sql), nombre


def test_acciones_usadas_en_el_codigo_estan_en_el_catalogo():
    catalogo = set()
    for archivo in (RAIZ / "migrations").glob("*.sql"):
        catalogo |= set(re.findall(r"\('([a-z_]+\.[a-z_]+\.[a-z_]+)',", archivo.read_text(encoding="utf-8")))
    accion = re.compile(r'"((?:%s)\.[a-z_]+\.[a-z_]+)"' % "|".join(MODULOS))
    usadas = set()
    for archivo in (RAIZ / "app").rglob("*.py"):
        codigo = archivo.read_text(encoding="utf-8")
        for llamada in re.finditer(r"(?:registrar_evento(?:_aparte)?|_auditar\w*)\(", codigo):
            # La acción es el primer texto de la llamada (o los dos de un condicional)
            usadas |= set(accion.findall(codigo[llamada.end():llamada.end() + 200].split("\n\n")[0])[:2])
    assert usadas, "no se encontraron acciones en el código"
    assert usadas <= catalogo, f"Acciones sin registrar en auditoria.acciones: {sorted(usadas - catalogo)}"


def test_usuario_fijado_en_otro_hilo_se_ve_en_el_contexto():
    """FastAPI corre la autenticación y el endpoint en hilos con copias del contexto."""
    token = contexto.iniciar(contexto.Contexto(origen="WEB", id_solicitud="abc12345"))
    try:
        hilo = threading.Thread(target=copy_context().run,
                                args=(contexto.fijar_usuario, {"id_usuario": "u1", "email": "a@b.com", "rol": "ADMIN"}))
        hilo.start()
        hilo.join()
        assert contexto.actual().email == "a@b.com"
        assert contexto.ajustes_pg() == {"origen": "WEB", "usuario": "a@b.com", "id_solicitud": "abc12345"}
    finally:
        contexto.restaurar(token)
    assert contexto.actual().email is None


def _app():
    app = FastAPI()
    app.add_middleware(MiddlewareContexto)

    @app.get("/ctx")
    def ver_contexto():
        c = contexto.actual()
        return {"origen": c.origen, "id_solicitud": c.id_solicitud, "ruta": c.ruta}

    return app


def test_middleware_asigna_id_y_origen():
    cliente = TestClient(_app())
    r = cliente.get("/ctx", headers={"Origin": "https://conciliacion.example"})
    datos = r.json()
    assert datos["origen"] == "WEB" and datos["ruta"] == "/ctx"
    assert r.headers["x-request-id"] == datos["id_solicitud"] and len(datos["id_solicitud"]) == 32

    r = cliente.get("/ctx", headers={"X-Request-ID": "pedido-12345"})
    assert r.json() == {"origen": "API", "id_solicitud": "pedido-12345", "ruta": "/ctx"}

    r = cliente.get("/ctx", headers={"X-Request-ID": "malo<script>"})
    assert r.json()["id_solicitud"] != "malo<script>"
