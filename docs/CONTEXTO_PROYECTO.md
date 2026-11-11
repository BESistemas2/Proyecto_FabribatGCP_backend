# Contexto del proyecto Fabribat GCP

Documento de traspaso para continuar el trabajo en otra conversación sin perder el hilo.
Actualizado: 11/11/2026.

## 1. Repositorios e infraestructura

- Backend: `Proyecto_FabribatGCP_backend` (FastAPI, Python 3.14, SQLAlchemy 2, PostgreSQL).
- Frontend: `Proyecto_FabribatGCP_frontend` (Next.js 15, React 19, Tailwind, Radix, React Query).
- GitHub: `BESistemas2/...` (hoy públicos; pendiente hacerlos privados).
- Despliegue: VM en GCP con Portainer y Traefik (red `web-network`, entrypoint `websecure`, certresolver `myresolver`), dominio `conciliacion.fabribat.com`.
- PostgreSQL en el contenedor `postgres_db`; MinIO como servicio `minio` en la red `minio-cs-apps_default`.
- Contenedor aparte `fabribat-scheduler` para tareas programadas (locks de PostgreSQL).
- Guías: `AGENTS.md` en cada repo; configuración de OpenCode en `opencode.json`.

## 2. Módulos y estado

| Módulo | Esquema PG | Estado |
| --- | --- | --- |
| bancos | `bancos` | Listo: carga de extractos (4 bancos), hash SHA-256, respaldo MinIO, espejo del libro de bancos de BC |
| conciliacion | `conciliacion` | Listo: motor de reglas, auto-match programado, actas, reportes, dashboard, corte inicial (migraciones hasta 008) |
| identidad | `identidad` | Básico: un rol por usuario (ver sección 5) |
| auditoria | `auditoria` | Fase 1 lista (migración 011): eventos con cadena de hash, catálogo de acciones, cambios por SQL directo, control de migraciones. Pendiente: pantalla y historial en el frontend |
| cobranzas | (por crear: `cobranzas`) | En diseño: migración desde AppSheet + MySQL + Cloud Run. Su migración pasa a ser la **012** (la 011 es auditoría) |

RPA de descarga de extractos (Playwright): en pausa hasta tener usuarios de banca en línea.

## 3. Documentos de referencia (claude.ai)

- Plan de migración de Cobranzas: https://claude.ai/artifact/GKnypvvdAXcfmKQJyTQtuD (fuente principal de decisiones de cobranzas).
- Plan del módulo de auditoría: https://claude.ai/artifact/Tj91BaNjW6mTWjYnZe6nsK (contrato para módulos nuevos en `AGENTS.md`).
- Reglas de cruce (memoria técnica): https://claude.ai/artifact/3qAfbgYvXPnJmefE721ysj
- Manual de usuario: https://claude.ai/artifact/XF2bxHZVCLjrzQuBqsXzJh
- Informe final (borrador): https://claude.ai/artifact/CskxXZDncfr6xsWRqLT2xc
- Presentación gerencial: https://claude.ai/artifact/8NcSUuv1iANcDgJPJbpePG

## 4. Cobranzas: decisiones tomadas

- Se reemplazan AppSheet, MySQL (`fabribat_cobranzas`, `fabribat_organizacion`) y el middleware de Cloud Run.
- Flujo: Ingresado → Enviado a Tesorería → Aprobado / Devuelto → Archivado y enviado al cliente. Anulado se mantiene, solo antes de la aprobación y con motivo; el cliente no se cambia en un comprobante creado.
- Correo al cliente automático cuando: el pago está publicado en el libro de clientes de BC y cada depósito/transferencia está vinculado a un movimiento del extracto.
- "SF" = saldo a favor → tipo de línea SALDO_A_FAVOR sin factura.
- DATAFAST = forma de pago tarjeta (abono a la línea de crédito); DIFERENCIA PAGO = Otros Abonos "Ajuste por redondeo".
- Bancos de cobranzas → cuentas: PRODUBANCO 01005007753, PICHINCHA 3146408404, GUAYAQUIL 43900692, PICHINCHA PTS 2100286209.
- Pago en BC: se liquida contra el documento interno (CFAC… en cuotas, número de factura si no hay cuotas). Nº de cliente en BC = RUC.
- Cierre de caja diario (entrega CJ01-AA-NNNNN: caja 1 matriz, año, secuencial anual) reemplaza el CAJA manual; el sistema reparte valores entre facturas.
- Mientras no haya envío por API: exportación de líneas del diario CASHRCPT para "Editar en Excel" (48 columnas; secciones TESORERIA2/3 por usuario de Tesorería, series T2…/T3…; contrapartida Bank Account CJ01; dimensiones 300COMERCIAL / 1200CAJA DEL DIA).
- Catálogos de BC (formas y términos de pago, vendedores, secciones de diario): espejo local diario; consulta en línea solo para saldo de facturas antes de exportar y confirmación de publicación.
- Intiza/DWH: se reemplaza por lectura directa de Dynamics.
- Pendientes abiertos: ver "Decisiones abiertas" del plan (A y B, códigos de forma de pago, liquidación de CH-PR y saldo a favor, Nº documento al publicar, UUID + id_legacy).

## 5. Permisos: punto de partida para el módulo de administración

### Lo que existe hoy en el backend (identidad)

- Tabla `identidad.usuarios`: email, nombre, **un solo** `rol`, activo.
- Roles: ADMIN, CONTABILIDAD, BASICO, RECEPTOR_MAIL.
- Reglas en código (`app/modulos/identidad/dependencies.py`): `ROLES_OPERACION = (ADMIN, CONTABILIDAD)`, RECEPTOR_MAIL sin acceso a la plataforma, `requerir_rol(...)`, `requerir_rol_admin`, `requerir_operador`.
- El rol se lee de la BD en cada petición (un cambio de rol aplica de inmediato).
- Frontend: menú y rutas filtradas por rol en `middleware.ts`, `layout.tsx` y `session.ts`.

### Lo que existe en AppSheet (MySQL fabribat_cobranzas)

| Tabla | Para qué sirve |
| --- | --- |
| Perfiles | Administrador, Admin Cobranzas, Crédito y Cobranzas, Tesorería |
| Usuarios | email, perfil, colaborador, activo, cobradorDynamics |
| VistasPorPerfil | qué pantallas ve cada perfil |
| Acciones | catálogo de acciones (flujo y de interfaz: AGREGAR_FC, AGREGAR_VALORES_LB…) |
| FlujoTrabajo | transición estado origen → acción → estado destino por perfil y tabla |
| PermisosPorEstado | editar/eliminar por tabla, perfil y estado |
| PermisosPorTablaPerfil | agregar/editar/eliminar por tabla y perfil |
| EstadoVisiblePorPerfil | qué estados de comprobante ve cada perfil (filtro de filas) |
| Secuencias | punto de venta/emisión por usuario y tipo (COMPROBANTE, CAJA) |

### Lo que falta y se debe diseñar

- Usuarios con más de un rol o con roles por módulo (ej. contabilidad en conciliación y tesorería en cobranzas).
- Permisos por módulo y por acción configurables desde la base, no en código.
- Restricción por datos (cuentas bancarias por usuario, estados visibles, cobrador asignado).
- Auditoría de cambios de permisos y de acciones sensibles.
- Pantalla de administración única para todos los módulos.

## 6. Entorno de pruebas (sesiones de Claude en la nube)

- Tests del backend: `python -m pytest -q` (48 pasan; `tests/conftest.py` pone variables ficticias).
- No usar `.env` reales en herramientas de terceros; OpenCode trabaja sobre una copia limpia (`git clone --depth 1`).
