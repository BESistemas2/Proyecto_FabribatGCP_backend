# Despliegue en una VM de GCP (Portainer + Caddy + HTTPS)

Arquitectura: una VM con Docker/Portainer y tres stacks. Solo el proxy (Caddy) se
publica a Internet; backend y frontend quedan en la red interna `fabribat`.

```
Internet ──443──> fabribat-proxy (Caddy, HTTPS automático)
                    ├─ /api/*  -> fabribat-api:8000      ──> PostgreSQL / MinIO (IP interna GCP)
                    └─ /*      -> fabribat-frontend:3000
```

En este documento el dominio de ejemplo es `conciliacion.fabribat.com`.

## 1. Crear la VM
- Compute Engine > Crear instancia: **e2-medium** (2 vCPU, 4 GB; construir el frontend
  necesita ~2 GB), Ubuntu 24.04 LTS, disco 30 GB, **misma región y VPC que la VM de PostgreSQL**.
- Red: reservar una **IP externa estática**; etiqueta de red `fabribat-web`.
- Identidad: cuenta de servicio con el rol **Secret Manager Secret Accessor**
  y alcance "Permitir acceso completo a las APIs de Cloud" (para leer los secretos sin JSON).

## 2. Firewall (VPC network > Firewall)
| Regla | Origen | Destino | Puertos |
|---|---|---|---|
| fabribat-web-https | 0.0.0.0/0 | etiqueta `fabribat-web` | tcp:80, tcp:443, udp:443 |
| fabribat-portainer | IP pública de la oficina | etiqueta `fabribat-web` | tcp:9443 |
| postgres-interno | IP interna de la VM nueva | VM de PostgreSQL | tcp:5432 |
| minio-interno | IP interna de la VM nueva | VM de MinIO | tcp:9000 |

Después, **eliminar** las reglas que abren 5432 y 9000 a Internet (0.0.0.0/0).
En PostgreSQL, `pg_hba.conf`: `hostssl fabribat_db <usuario> <IP_INTERNA_VM>/32 scram-sha-256`.

## 3. Docker y Portainer en la VM
```bash
curl -fsSL https://get.docker.com | sudo sh
sudo docker volume create portainer_data
sudo docker run -d --name portainer --restart=always -p 9443:9443 \
  -v /var/run/docker.sock:/var/run/docker.sock -v portainer_data:/data \
  portainer/portainer-ce:lts
```
Entrar a `https://IP_VM:9443` desde la oficina y crear el usuario administrador.

## 4. DNS y Google
- En el DNS de fabribat.com: registro **A** `conciliacion` -> IP estática de la VM.
  Comprobar con `nslookup conciliacion.fabribat.com` antes de seguir.
- Google Cloud Console > APIs y servicios > Credenciales > Client ID OAuth >
  **Orígenes autorizados de JavaScript**: `https://conciliacion.fabribat.com`.

## 5. Stacks en Portainer (en este orden)
Para repos privados: Stacks > Add stack > Repository, con autenticación
(usuario de GitHub + token *fine-grained* de solo lectura de "Contents").

1. **fabribat-proxy** — repo backend, compose path `deploy/proxy/docker-compose.yml`
   - `DOMINIO=conciliacion.fabribat.com`
   - `ACME_EMAIL=sistemas@fabribat.com`
2. **fabribat-api** — repo backend, compose path `docker-compose.yml`
   - Variables de `.env.example` ("Load variables from .env file"), con:
     - `CORS_ORIGINS=https://conciliacion.fabribat.com`
     - `PG_G_HOST` / `MINIO_G_ENDPOINT` = **IP interna** de la VM de BD, `PG_G_SSLMODE=require`
     - `GCP_PROJECT_ID=<proyecto>` y dejar vacíos los secretos que estén en Secret Manager
3. **fabribat-frontend** — repo frontend, compose path `docker-compose.yml`
   - `NEXT_PUBLIC_API_BASE_URL=https://conciliacion.fabribat.com/api/v1`
   - `NEXT_PUBLIC_GOOGLE_CLIENT_ID=<mismo GOOGLE_CLIENT_ID del backend>`

## 6. Verificación
- Logs de `fabribat-proxy`: debe aparecer "certificate obtained successfully".
- Portainer > Containers: `fabribat-api` y `fabribat-frontend` en estado *healthy*.
- Abrir `https://conciliacion.fabribat.com`, iniciar sesión con Google y cargar un extracto.

## Actualizar a una versión nueva
`git push` a `main` y en Portainer, en el stack: **Pull and redeploy** (marcar *Re-build*
para el frontend, porque las variables `NEXT_PUBLIC_*` se fijan al construir).
