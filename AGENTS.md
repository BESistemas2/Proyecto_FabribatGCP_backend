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

## Auditoría (obligatoria en cada módulo)

Un módulo nuevo (cobranzas y los que vengan) queda auditado si cumple estos puntos. Una tarea no está
terminada si sus acciones no quedan auditadas. Plan completo: documento "Plan del módulo de auditoría".

1. **Acciones en el catálogo.** La migración del módulo agrega sus acciones a `auditoria.acciones`
   con el formato `modulo.entidad.verbo` y su nivel (`INFORMATIVO`, `IMPORTANTE`, `CRITICO`), por ejemplo
   `('cobranzas.comprobante.aprobar', 'cobranzas', 'Aprobar comprobante', 'CRITICO')`.
   `tests/test_auditoria.py` falla si el código usa una acción que no está en ninguna migración.
2. **Un evento por acción que cambia algo**, en el servicio y ANTES del `commit`, en la misma sesión:
   ```python
   from app.modulos.auditoria.servicio import diferencias, registrar_evento
   registrar_evento(db, "cobranzas.comprobante.aprobar", f"Aprobó el comprobante {numero}",
                    entidad="comprobante", id_entidad=c.id_comprobante,
                    cambios={"estado": ["ENVIADO", "APROBADO"]}, datos={"motivo": motivo})
   db.commit()
   ```
   Quién, IP, origen e id de solicitud salen solos del contexto: no se pasan.
   Los registros hijos llevan `id_entidad_padre` (p. ej. un valor recibido apunta a su comprobante).
   `diferencias(antes, despues)` arma `cambios` con solo los campos que cambiaron.
3. **Rechazos y errores** que deben quedar aunque la operación no se guarde: `registrar_evento_aparte(...)`
   con `resultado="ERROR"` o `"DENEGADO"`, después del `rollback`.
4. **Procesos masivos = un evento** con el resumen en `datos` (no uno por fila). Las lecturas no se
   registran, salvo descargas o envíos de documentos con datos de clientes.
5. **Nunca secretos** en `cambios` ni `datos` (las claves con pass/token/secret/clave se ocultan solas).
6. **Tablas críticas del módulo** (configuración, aprobaciones): en su migración se les agrega el trigger
   `tg_auditoria_sql_directo` (ver `011_auditoria.sql`), que registra los cambios hechos fuera de la app.
7. **Cada migración termina registrándose**:
   `INSERT INTO public.schema_migrations (numero, archivo) VALUES (NN, '0NN_xxx.sql') ON CONFLICT (numero) DO NOTHING;`

## Comandos

- Tests: `python -m pytest -q` (no necesitan `.env`: `tests/conftest.py` pone valores ficticios).
- Un archivo: `python -m pytest tests/test_xxx.py -q`.
