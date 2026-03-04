# Deployment Guide

Hosting: Scaleway Serverless Containers
Domain: `api.snippets.eu`
DNS: Cloudflare
HTTPS: Handled by Scaleway (automatic TLS)

No server to manage — Scaleway runs the Docker image directly.

## Prerequisites

- [Scaleway CLI (`scw`)](https://github.com/scaleway/scaleway-cli) installed and configured
- A Scaleway account with a project
- Cloudflare managing DNS for `snippets.eu`
- An external PostgreSQL database accessible from the internet

```bash
# Install and configure the CLI
scw init
```

## 1. Create a Container Registry namespace

```bash
scw registry namespace create name=snippets-backend
```

Note the `endpoint` from the output (e.g. `rg.pl-waw.scw.cloud/snippets-backend`).

Log in to the registry:

```bash
scw registry login
```

## 2. Build and push the Docker image

```bash
docker build -t rg.pl-waw.scw.cloud/snippets-backend/api:latest .
docker push rg.pl-waw.scw.cloud/snippets-backend/api:latest
```

Replace `rg.pl-waw.scw.cloud` with your actual registry endpoint if in a different region.

## 3. Create the Serverless Container

Via the Scaleway console (recommended for first setup):

1. Go to **Serverless > Containers** and create a new container
2. Select the `snippets-backend/api:latest` image from your registry
3. Set **port** to `8000`
4. Set **environment variables**:

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | `postgresql://user:password@host:port/dbname?sslmode=require` |
| `JWT_SECRET` | Generate with: `openssl rand -hex 32` |
| `JWT_EXPIRY_HOURS` | `720` |
| `ALLOWED_ORIGINS` | (leave empty — frontend is a CLI app, no browser CORS needed) |
| `DEBUG` | `false` |
| `APP_ENV` | `prod` |
| `INVITE_ADMIN` | `adam` |

Use **secret variables** for `DATABASE_URL` and `JWT_SECRET`.

5. Set resources (min 128 MB memory, adjust as needed)
6. Set **min scale to 1** if you want the container always running (avoids cold starts), or 0 for scale-to-zero
7. Deploy

Or via CLI:

```bash
scw container container create \
  name=snippets-backend \
  namespace-id=<your-namespace-id> \
  registry-image=rg.pl-waw.scw.cloud/snippets-backend/api:latest \
  port=8000 \
  min-scale=1 \
  max-scale=3 \
  memory-limit=256 \
  environment-variables.DATABASE_URL="postgresql://user:password@host:port/dbname?sslmode=require" \
  environment-variables.JWT_EXPIRY_HOURS=720 \
  environment-variables.ALLOWED_ORIGINS="" \
  environment-variables.DEBUG=false \
  environment-variables.APP_ENV=prod \
  environment-variables.INVITE_ADMIN=adam \
  secret-environment-variables.0.key=JWT_SECRET \
  secret-environment-variables.0.value="<your-jwt-secret>"
```

## 4. Set up the custom domain

### In Scaleway

1. Go to your container's settings > **Custom Domains**
2. Add `api.snippets.eu`
3. Scaleway will show you a CNAME target (e.g. `<container-id>.functions.fnc.pl-waw.scw.cloud`)

### In Cloudflare

Create a CNAME record:

| Type | Name | Content | Proxy |
|------|------|---------|-------|
| CNAME | `api` | `<scaleway-cname-target>` | DNS only (grey cloud) |

Use **DNS only** (grey cloud) so Scaleway can provision its TLS certificate.

Wait for DNS propagation (usually a few minutes).

## 5. Verify

```bash
curl https://api.snippets.eu/health
# {"status":"ok"}
```

## Updating

Build, push, and redeploy:

```bash
docker build -t rg.pl-waw.scw.cloud/snippets-backend/api:latest .
docker push rg.pl-waw.scw.cloud/snippets-backend/api:latest
scw container container deploy <container-id>
```

Schema migrations run automatically on startup (all DDL is idempotent).

## Notes

- **No Caddy needed** — Scaleway handles TLS termination automatically.
- **No docker-compose in production** — only the single backend container runs on Scaleway. The `docker-compose.yml` is for local dev or VM-based deployments.
- The `.env.prod` file is not used in this setup — environment variables are configured directly in Scaleway.
- The Dockerfile runs uvicorn with 4 workers. Scaleway's autoscaling (min/max scale) adds additional container instances on top of that.

## Troubleshooting

**Container won't start:**
- Check container logs in Scaleway console or `scw container container logs <container-id>`
- Verify `DATABASE_URL` is reachable from Scaleway's network (the DB must allow external connections)

**Custom domain not working:**
- Ensure the CNAME record in Cloudflare is set to DNS only (grey cloud, not orange)
- Check that the domain is verified in Scaleway's custom domains settings
- Wait a few minutes for DNS propagation

**CORS errors:**
- The primary frontend is a CLI app (`../cli.snippets`), so CORS is generally not needed. If you add a web frontend later, set `ALLOWED_ORIGINS` to match its exact origin (including scheme, no trailing slash)

**Cold starts:**
- Set min-scale to 1 to keep at least one instance warm
- First request after scale-to-zero may take a few seconds
