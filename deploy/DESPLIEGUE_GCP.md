# Despliegue en una VM de GCP (Portainer + Traefik + HTTPS)

Arquitectura: la VM ya tiene Docker, Portainer y **Traefik**. Se agregan dos stacks que
Traefik publica por etiquetas (labels); ninguno abre puertos públicos propios.

```
Internet ──443──> Traefik (existente, certificado Let's Encrypt)
                    ├─ Host(DOMINIO) && PathPrefix(/api) -> fabribat-api:8000 ──> PostgreSQL / MinIO (IP interna)
                    └─ Host(DOMINIO)                     -> fabribat-frontend:3000
```

Datos de Traefik que hay que confirmar (ver su docker-compose o traefik.yml) y poner
como variables en AMBOS stacks si difieren de los valores por defecto:

| Variable | Por defecto | Dónde se ve en Traefik |
|---|---|---|
| `TRAEFIK_NETWORK` | `web-network` | red Docker a la que está conectado el contenedor de Traefik |
| `TRAEFIK_ENTRYPOINT` | `websecure` | `entryPoints` del puerto 443 |
| `TRAEFIK_CERTRESOLVER` | `myresolver` | `certificatesResolvers` |

En este documento el dominio de ejemplo es `conciliacion.fabribat.com`.

## 1. VM
- Construir el frontend necesita ~2 GB de RAM libres; si la VM tiene menos, ampliarla o
  construir la imagen en otra máquina.
- Para leer Secret Manager sin JSON: la cuenta de servicio de la VM necesita el rol
  **Secret Manager Secret Accessor** y el alcance "Permitir acceso completo a las APIs de Cloud"
  (se cambia con la VM detenida: Editar > Identidad y acceso a la API).

## 2. Firewall (VPC network > Firewall)
| Regla | Origen | Destino | Puertos |
|---|---|---|---|
| (ya existe para Traefik) | 0.0.0.0/0 | VM de Traefik | tcp:80, tcp:443 |
| Portainer | solo IP pública de la oficina | VM de Traefik | tcp:9443 |
| postgres-interno | IP interna de la VM nueva | VM de PostgreSQL | tcp:5432 |
| minio-interno | IP interna de la VM nueva | VM de MinIO | tcp:9000 |

Después, **eliminar** las reglas que abren 5432 y 9000 a Internet (0.0.0.0/0).
En PostgreSQL, `pg_hba.conf`: `hostssl fabribat_db <usuario> <IP_INTERNA_VM>/32 scram-sha-256`.

## 3. DNS y Google
- En el DNS de fabribat.com: registro **A** `conciliacion` -> IP estática de la VM.
  Comprobar con `nslookup conciliacion.fabribat.com` antes de seguir.
- Google Cloud Console > APIs y servicios > Credenciales > Client ID OAuth >
  **Orígenes autorizados de JavaScript**: `https://conciliacion.fabribat.com`.

## 4. Stacks en Portainer
Para repos privados: Stacks > Add stack > Repository, con autenticación
(usuario de GitHub + token *fine-grained* de solo lectura de "Contents").

1. **fabribat-api** — repo backend, compose path `docker-compose.yml`
   - Variables de `.env.example` ("Load variables from .env file"), más `DOMINIO`
     (y las `TRAEFIK_*` si aplican), con:
     - `CORS_ORIGINS=https://conciliacion.fabribat.com`
     - `PG_G_HOST` / `MINIO_G_ENDPOINT` = **IP interna** de la VM de BD, `PG_G_SSLMODE=require`
     - `GCP_PROJECT_ID=<proyecto>` y dejar vacíos los secretos que estén en Secret Manager
2. **fabribat-frontend** — repo frontend, compose path `docker-compose.yml`
   - `DOMINIO=conciliacion.fabribat.com` (y las `TRAEFIK_*` si aplican)
   - `NEXT_PUBLIC_API_BASE_URL=https://conciliacion.fabribat.com/api/v1`
   - `NEXT_PUBLIC_GOOGLE_CLIENT_ID=<mismo GOOGLE_CLIENT_ID del backend>`

## 5. Verificación
- Dashboard o logs de Traefik: routers `fabribat-api` y `fabribat-web` activos y sin errores
  de certificado.
- Portainer > Containers: `fabribat-api` y `fabribat-frontend` en estado *healthy*.
- Abrir `https://conciliacion.fabribat.com`, iniciar sesión con Google y cargar un extracto.

## Actualizar a una versión nueva
`git push` a `main` y en Portainer, en el stack: **Pull and redeploy** (marcar *Re-build*
para el frontend, porque las variables `NEXT_PUBLIC_*` se fijan al construir).
