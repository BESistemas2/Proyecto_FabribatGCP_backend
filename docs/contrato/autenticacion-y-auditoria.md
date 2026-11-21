# Contrato: autenticación y auditoría (backend Python)

Describe cómo funcionan **hoy** la sesión y la auditoría del backend FastAPI, para que la versión
.NET (`Proyecto_FabribatGCP_backend_net`) se comporte igual. El contrato HTTP completo está en
[`openapi.json`](openapi.json) (se regenera con `python scripts/exportar_openapi.py`).

Código de referencia:

- `app/core/config.py` (variables `JWT_*`)
- `app/modulos/identidad/service.py` (`AuthService`), `dependencies.py`, `router.py`
- `app/modulos/auditoria/servicio.py`
- `migrations/011_auditoria.sql`

---

## 1. Token de sesión

### Cómo se obtiene

`POST /api/v1/identidad/auth/google` con un ID token de Google (Google Identity Services) en el cuerpo.
Se acepta en cualquiera de estos campos; se usa el primero con valor, en este orden:
`credential`, `token`, `id_token`. Si no viene ninguno: `400 "Se requiere el ID Token de Google"`.

El backend, en este orden:

1. Verifica el ID token con `google.oauth2.id_token.verify_oauth2_token` (firma, expiración y
   `aud == GOOGLE_CLIENT_ID`). Inválido o expirado → `401`. Si no se pueden descargar los
   certificados de Google → `503`.
2. Exige `email_verified` (`403` si no) y que el dominio del correo **y** el claim `hd` sean
   `ALLOWED_DOMAIN` (por defecto `bateriasecuador.com`) → `403` si no.
3. Busca el correo en `identidad.usuarios`: no existe → `403`; `activo = false` → `403`;
   rol `RECEPTOR_MAIL` → `403` (ese rol solo recibe correos).
4. Registra `identidad.sesion.iniciar` en la auditoría, hace `commit` y emite el JWT.

Cada rechazo de los pasos 1 a 3 (no el `503`) se audita con `registrar_evento_aparte`
(`identidad.sesion.rechazar`, `resultado="DENEGADO"`, `datos={"motivo": ...}`).

### Respuesta del login

La ruta no declara `response_model`, así que `openapi.json` no describe este cuerpo. Su forma es:

```json
{
  "access_token": "<JWT>",
  "token_type": "bearer",
  "expires_in": 28800,
  "user": {
    "id_usuario": "<uuid>",
    "email": "persona@bateriasecuador.com",
    "name": "Nombre Apellido",
    "picture": "https://... o null",
    "rol": "ADMIN | CONTABILIDAD | BASICO",
    "domain": "bateriasecuador.com"
  }
}
```

`expires_in` = `JWT_EXPIRE_HOURS * 3600` segundos.

### Formato del JWT

| Aspecto | Valor |
|---|---|
| Librería | PyJWT (`jwt.encode` / `jwt.decode`) |
| Algoritmo | `HS256` (fijo en `config.JWT_ALGORITHM`) |
| Clave | `JWT_SECRET_KEY` (ver abajo) |
| Emisor (`iss`) | `fabribat-api` (fijo en `config.JWT_ISSUER`) |
| Audiencia (`aud`) | No se emite ni se valida |
| Expiración | `JWT_EXPIRE_HOURS` horas desde la emisión; por defecto **8** |
| Renovación | No hay refresh token: al expirar se vuelve a iniciar sesión con Google |

Claims del payload:

| Claim | Tipo | Contenido |
|---|---|---|
| `sub` | string | `id_usuario` (UUID en texto) de `identidad.usuarios` |
| `email` | string | Correo en minúsculas |
| `name` | string \| null | `nombre` de la BD; si está vacío, el `name` de Google |
| `picture` | string \| null | Foto de Google |
| `rol` | string | Rol en la BD al iniciar sesión (informativo, ver abajo) |
| `iat` | NumericDate | Momento de emisión (UTC, segundos epoch) |
| `exp` | NumericDate | `iat + JWT_EXPIRE_HOURS` |
| `iss` | string | `fabribat-api` |

### De dónde sale la clave

`JWT_SECRET_KEY` se lee con `require_env`: primero de la variable de entorno y, si no existe y
`GCP_PROJECT_ID` está definido, de Google Secret Manager (secreto `<GSM_SECRET_PREFIX>JWT_SECRET_KEY`,
versión `latest`). En Docker/Portainer llega por el `stack.env` del despliegue.
Es obligatoria (sin ella la app no arranca) y debe tener **al menos 32 caracteres**
(se sugiere `openssl rand -hex 32`). El valor real no está en este repositorio; los tests usan uno ficticio.

Para que .NET valide los tokens de Python (o al revés) durante la convivencia, ambos deben usar
la misma `JWT_SECRET_KEY`, el mismo algoritmo y el mismo emisor.

### Cómo se valida en cada solicitud

Dependencia `obtener_usuario_autenticado` (`identidad/dependencies.py`):

1. Exige `Authorization: Bearer <token>`; si falta → `401 "Token no proporcionado"`.
2. `jwt.decode(token, JWT_SECRET_KEY, algorithms=["HS256"], issuer="fabribat-api",
   options={"require": ["exp", "sub"]})`. Sin margen de reloj (leeway 0).
   Expirado → `401 "El token de sesión ha expirado"`; cualquier otro error
   (firma, `iss`, falta `exp`/`sub`) → `401 "Token de sesión inválido"`.
3. `sub` debe ser un UUID válido → si no, `401`.
4. Se consulta el usuario en la BD **en cada solicitud**: si no existe o está inactivo → `401`;
   si su rol es `RECEPTOR_MAIL` → `403`.
5. **El rol y el nombre se toman de la BD, no del token**: un cambio de rol o una desactivación
   aplican de inmediato. El claim `rol` del token es solo informativo.
6. Fija el usuario en el contexto de la solicitud (para la auditoría) y en la transacción de
   PostgreSQL (`set_config('app.usuario', email, true)`).

Todas las respuestas `401` llevan `WWW-Authenticate: Bearer`.

Roles: `requerir_rol(*roles)` y `requerir_rol_admin` devuelven `403` si el rol no alcanza, y
registran `sistema.permiso.denegar` (`resultado="DENEGADO"`) con `registrar_evento_aparte`.
`ADMIN` puede todo; `CONTABILIDAD` opera (cargar extractos, conciliar, cerrar y enviar actas);
`BASICO` solo consulta.

---

## 2. Auditoría

Módulo `app.modulos.auditoria.servicio`. Los eventos van a `auditoria.eventos` (migración 011):
tabla de solo inserción, particionada por mes, con cadena de hash SHA-256 calculada por un trigger.

### `registrar_evento`

```python
def registrar_evento(
    db: Session,
    accion: str,
    resumen: str,
    *,
    entidad: Optional[str] = None,
    id_entidad: Any = None,
    id_entidad_padre: Any = None,
    cambios: Optional[Mapping[str, Any]] = None,
    datos: Optional[Mapping[str, Any]] = None,
    resultado: str = "OK",
    usuario: Optional[Mapping[str, Any]] = None,
) -> None
```

Agrega el evento a la transacción de `db`. **No hace commit**: se llama antes del `commit` del
servicio, en la misma sesión. Si la operación se revierte, el evento también; si el evento falla,
la operación no se guarda.

| Parámetro | Comportamiento |
|---|---|
| `accion` | Código `modulo.entidad.verbo`. Debe existir en `auditoria.acciones`; si no, el trigger lanza una excepción y la transacción falla. El trigger deriva `modulo` (primer segmento) y `nivel` (del catálogo). |
| `resumen` | Texto legible. El trigger lo recorta a 2000 caracteres. |
| `entidad`, `id_entidad`, `id_entidad_padre` | Se guardan como texto (`str(...)`); `None` queda `NULL`. |
| `cambios` | Normalmente el resultado de `diferencias(...)`: `{campo: [antes, después]}`. |
| `datos` | Información adicional libre (motivo, resumen de un proceso masivo, etc.). |
| `resultado` | `"OK"`, `"ERROR"` o `"DENEGADO"`. Otro valor → `ValueError` antes de tocar la BD. |
| `usuario` | Opcional. Mapping con `id_usuario`, `email`, `rol`: cada campo que traiga valor reemplaza al del contexto. Se usa cuando aún no hay usuario en el contexto (login, accesos denegados). |

Lo que se completa solo, desde `app.core.contexto.actual()` (lo llena el middleware de cada
solicitud y la dependencia de autenticación; el scheduler fija el suyo):
`id_usuario`, `email`, `rol` (si `usuario` no los trae), `origen`, `ip`, `user_agent`, `id_solicitud`.
`origen` es uno de `WEB`, `API`, `SCHEDULER`, `SQL_DIRECTO`, `MIGRADO`; cualquier otro valor se guarda como `API`.

Serialización de `cambios` y `datos`:

- Un mapping vacío o `None` se guarda como `NULL`.
- Las claves cuyo nombre contiene `pass`, `secret`, `token`, `clave`, `credencial`, `mfa`,
  `api_key` o `authorization` (sin distinguir mayúsculas, en cualquier nivel de anidación) se
  reemplazan por `"***"`.
- JSON con `sort_keys=True` y `ensure_ascii=False`. Los `Decimal` se escriben como texto
  (`"1234.50"`), las fechas en ISO 8601, los UUID y enums como texto, los set y tuple como lista;
  cualquier otro tipo, con `str()`.

Si la tabla `auditoria.eventos` no existe (migración 011 sin ejecutar), la función **no hace nada**
y deja un aviso en el log. Lo vuelve a comprobar como máximo una vez por minuto; cuando la
encuentra, deja de comprobarlo.

Concurrencia: el trigger toma `FOR UPDATE` sobre la única fila de `auditoria.cadena`, así que las
inserciones de eventos se serializan hasta el fin de cada transacción.

Ejemplo:

```python
from app.modulos.auditoria.servicio import diferencias, registrar_evento

registrar_evento(db, "cobranzas.comprobante.aprobar", f"Aprobó el comprobante {numero}",
                 entidad="comprobante", id_entidad=c.id_comprobante,
                 cambios={"estado": ["ENVIADO", "APROBADO"]}, datos={"motivo": motivo})
db.commit()
```

### `registrar_evento_aparte`

```python
def registrar_evento_aparte(accion: str, resumen: str, **kwargs: Any) -> None
```

- Abre su propia sesión (`SessionLocalPG()`), fija `SET LOCAL lock_timeout = '5s'`, llama a
  `registrar_evento(db, accion, resumen, **kwargs)` y hace `commit`.
- `kwargs` son los mismos parámetros con nombre de `registrar_evento`
  (`entidad`, `resultado`, `usuario`, `datos`, ...).
- **Nunca lanza excepciones**: cualquier error se escribe en el log y la función regresa.
- Uso: eventos que deben quedar aunque la operación principal no se guarde (accesos denegados,
  inicios de sesión rechazados, errores). Se llama después del `rollback` de la sesión principal.
- No llamarla con eventos pendientes en la sesión principal: esa sesión tiene bloqueada la fila
  de la cadena y la sesión aparte esperaría hasta 5 s, fallaría y el evento se perdería (solo en el log).

### `diferencias`

```python
def diferencias(antes: Mapping[str, Any], despues: Mapping[str, Any],
                omitir: Iterable[str] = ()) -> Dict[str, list]
```

- Devuelve `{campo: [valor_antes, valor_después]}` **solo con los campos que cambiaron**, en
  orden alfabético. Si nada cambió, devuelve `{}`, que `registrar_evento` guarda como `NULL`.
- Recorre la unión de claves de ambos mappings; una clave que falta en uno cuenta como `None`.
- `omitir`: campos que no se comparan (p. ej. `actualizado_en`).
- Los valores devueltos son los originales (sin convertir); la conversión a JSON la hace
  `registrar_evento`.
- Reglas de comparación:
  - `str`, `int`, `float`, `bool` y `None` se comparan tal cual (`Decimal("10") == 10` es igual).
  - `Decimal` se normaliza: `Decimal("10.50")` y `Decimal("10.5")` son iguales.
  - Listas, tuplas y sets se comparan sin importar el orden.
  - Fechas, UUID y enums se comparan por su texto (ISO, `str`, `.value`).

```python
diferencias({"estado": "ENVIADO", "monto": Decimal("10.50")},
            {"estado": "APROBADO", "monto": Decimal("10.5")})
# {"estado": ["ENVIADO", "APROBADO"]}
```
