# Deployment Guide

Hosting: Scaleway container instance
Domain: `backend.snippets.eu`
DNS: Cloudflare (DNS-only mode, grey cloud)
HTTPS: Caddy (auto Let's Encrypt)

## 1. Provision a server on Scaleway

Any small instance works (e.g. DEV1-S). Install Docker and Docker Compose.

```bash
apt update && apt install -y docker.io docker-compose-plugin
```

## 2. Clone the repo

```bash
git clone <repo-url> /opt/snippets-backend
cd /opt/snippets-backend
```

## 3. Configure DNS

In Cloudflare, create an A record:

| Type | Name | Content | Proxy |
|------|------|---------|-------|
| A | `backend.snippets` | `<server-ip>` | DNS only (grey cloud) |

Caddy needs to reach Let's Encrypt directly, so the proxy must be off.

## 4. Create `.env.prod`

```bash
cp .env.example .env.prod
```

Edit `.env.prod` with production values:

```
DATABASE_URL=postgresql://user:password@host:port/dbname?sslmode=require
JWT_SECRET=<generate with: openssl rand -hex 32>
JWT_EXPIRY_HOURS=720
ALLOWED_ORIGINS=https://snippets.eu
DEBUG=false
```

**Secrets to set:**

| Variable | What to do |
|----------|------------|
| `DATABASE_URL` | Point to your production PostgreSQL instance |
| `JWT_SECRET` | Generate: `openssl rand -hex 32`. Never reuse the dev value |
| `ALLOWED_ORIGINS` | Set to your React frontend origin (e.g. `https://snippets.eu`). Leave empty if CLI-only |
| `DEBUG` | Must be `false` in production |

## 5. Open firewall ports

Caddy needs ports 80 (HTTP, for ACME challenges) and 443 (HTTPS):

```bash
# Scaleway security group or ufw
ufw allow 80/tcp
ufw allow 443/tcp
```

Port 8000 should **not** be open publicly — it's bound to `127.0.0.1` in `docker-compose.yml`.

## 6. Deploy

```bash
ENV=prod docker compose up --build -d
```

Caddy will automatically obtain a TLS certificate for `backend.snippets.eu` on first request.

## 7. Verify

```bash
curl https://backend.snippets.eu/health
# {"status":"ok"}
```

## Updating

```bash
cd /opt/snippets-backend
git pull
ENV=prod docker compose up --build -d
```

Schema migrations run automatically on startup (all DDL is idempotent).

## Troubleshooting

**Caddy can't get a certificate:**
- Check that Cloudflare proxy is off (grey cloud, not orange)
- Check that ports 80 and 443 are open in the Scaleway security group
- Check logs: `docker compose logs caddy`

**Backend won't start:**
- Check logs: `docker compose logs backend`
- Verify `DATABASE_URL` is reachable from the container

**CORS errors in browser:**
- Ensure `ALLOWED_ORIGINS` in `.env.prod` matches the exact frontend origin (including scheme, no trailing slash)
