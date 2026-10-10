# HTTPS / TLS Termination for the Platform

The server is currently served in plaintext (`http://<YOUR_SERVER_IP>`). Submitting
onboarding tokens, wallet signatures, or API keys over HTTP is unsafe, and external
Runners should only be registered with `https://` endpoints. This document covers two
ways to terminate TLS in front of the existing services.

The containers are unchanged: `web` is published on `:3000`, `api` on `:8000`, and the
signer (`:8020`) / runner (`:8010`) remain loopback-only and are never exposed publicly.

---

## Option A — Cloudflare Tunnel (recommended for experiments, no domain needed)

Works on a bare IP, no certificate management, gives a public `https://` URL in seconds.

```bash
# On the host running the compose stack:
cloudflared tunnel --url http://localhost:80
# Prints something like: https://<name>.trycloudflare.com
```

Then point the platform at that HTTPS origin:

- `web` env: `NEXT_PUBLIC_API_URL=https://<name>.trycloudflare.com`
- `api` env: `CORS_ORIGINS=https://<name>.trycloudflare.com`
- Re-create the web container (`docker compose up -d --force-recreate web`).
- External runners register their `endpoint` as the corresponding `https://` URL.

For a stable URL, create a named tunnel and add a route in a Cloudflare zone you own.

---

## Option B — Caddy with a real domain (recommended for production)

`infra/Caddyfile` terminates TLS and auto-renews Let's Encrypt certificates.

1. Point a DNS `A`/`AAAA` record (e.g. `agent.example.com`) at the host's public IP.
2. Open ports `80` and `443`.
3. Edit `infra/Caddyfile`: set the domain and the `email`.
4. Run Caddy (it binds `80`/`443`):

   ```bash
   caddy run --config infra/Caddyfile
   # or containerised:
   docker run -d -p 80:80 -p 443:443 \
     -v $PWD/infra/Caddyfile:/etc/caddy/Caddyfile caddy
   ```

5. Update origins as in Option A (`NEXT_PUBLIC_API_URL`, `CORS_ORIGINS`) to the domain.

> If you prefer to keep the existing nginx, add a `listen 443 ssl;` server block and
> obtain certs with `certbot --nginx`, then proxy `/api` → `localhost:8000` and `/` →
> `localhost:3000`. The routing intent is identical to `infra/Caddyfile`.

---

## Origin / CORS checklist

- [ ] `web` `NEXT_PUBLIC_API_URL` = the public `https://` origin (not `http://<YOUR_SERVER_IP>`).
- [ ] `api` `CORS_ORIGINS` includes that `https://` origin (default is `localhost` only).
- [ ] External Runner `endpoint` registered as `https://`.
- [ ] No secrets (onboarding token, wallet signature, API key) submitted over `http://`.

See `docs/EXTERNAL_RUNNER_CONTRACT.md` §5 for the endpoint HTTPS requirement.
