# AGENTS.md — Backend Fabribat (FastAPI)

Guía para agentes de código (OpenCode u otros). Léela completa antes de cada tarea.

## Qué es este proyecto

API FastAPI de Baterías Ecuador / Fabribat. Módulos en `app/modulos/`:

- `bancos`: carga de extractos bancarios, cuentas y movimientos (esquema PostgreSQL `bancos`).
- `conciliacion`: motor de cruce banco vs Business Central, actas, dashboard (esquema `conciliacion`).
- `identidad`: usuarios y roles (esquema `identidad`).
- `cobranzas`: **en migración** desde AppSheet + MySQL. El código actual en Flask es la versión vieja; se reescribe en FastAPI.

Python 3.14, FastAPI, SQLAlchemy 2, Pydantic 2, PostgreSQL. `app/scheduler.py` corre tareas programadas.

## Reglas de seguridad (obligatorias)

- Nunca leas, muestres, copies ni pidas archivos `.env`, `*.env`, llaves, credenciales ni `deploy/*.env`.
- No intentes conectarte a Business Central, PostgreSQL, MySQL, MinIO ni a ningún servidor. Esta copia no tiene credenciales a propósito.
- No uses datos reales de clientes, extractos ni dumps. En tests, solo datos inventados.
- No escribas secretos, IPs, tenant IDs ni contraseñas en el código. Toda configuración va en `app/core/config.py` leyendo variables de entorno.
- No agregues dependencias a `requirements.txt` sin que la tarea lo diga.

## Cómo se trabaja

1. Cada tarea está en `tareas/TNN.md`. Haz solo lo que dice; si algo no está claro, pregunta en vez de suponer.
2. Trabaja en la rama `cobranzas/TNN-descripcion-corta` (créala desde `main`).
3. No toques `conciliacion`, `bancos`, `identidad`, `migrations/` ni `deploy/` salvo que la tarea lo pida.
4. Antes de terminar: `python -m pytest -q` debe pasar completo.
5. Haz commits pequeños con mensajes en español. No hagas push a `main`.
6. Al terminar, resume: archivos cambiados, decisiones tomadas y qué quedó pendiente.

## Convenciones de código

- Estructura por módulo: `router.py` (endpoints), `schemas.py` (Pydantic), `service.py` (lógica), `repository.py` (consultas), `models.py` (SQLAlchemy). El modelo a copiar es `app/modulos/conciliacion/`.
- Nombres en español y snake_case (`id_cliente`, `fecha_emision`). Tablas en el esquema PostgreSQL del módulo (`{"schema": "cobranzas"}`).
- Dinero siempre `Decimal` y columnas `Numeric(14, 2)`. Nunca `float`.
- IDs `UUID(as_uuid=True)` salvo que la tarea indique otra cosa.
- Sesión de base de datos: `Depends(get_db_pg)`. Autenticación y roles: igual que en `conciliacion/router.py`.
- Errores de negocio con `HTTPException` (422 validación, 404 no existe, 409 conflicto de estado).
- Fechas en zona `America/Guayaquil`.
- Docstrings y comentarios breves, en español.

## Comandos

- Tests: `python -m pytest -q` (no necesitan `.env`: `tests/conftest.py` pone valores ficticios).
- Un archivo: `python -m pytest tests/test_xxx.py -q`.
