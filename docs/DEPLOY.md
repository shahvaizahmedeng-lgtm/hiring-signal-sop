# Full app deploy (frontend + backend)

This project is **one FastAPI process** that serves both:

| Path | Role |
|------|------|
| `GET /` | Frontend demo UI (`src/static/index.html`) |
| `GET /health` | Backend health |
| `POST /run` | Backend sourcing pipeline |
| `GET /docs` | OpenAPI docs |

You do **not** need separate frontend and backend hosts.

---

## Public URL right now (free Cloudflare tunnel)

From the project root:

```bash
./scripts/start-public.sh
```

Stop:

```bash
./scripts/stop-public.sh
```

Your Mac must stay awake while the tunnel is running.

---

## Optional: Docker (any free container host)

```bash
docker build -t hiring-signal .
docker run --rm -p 8000:8000 --env-file .env hiring-signal
```

Then open `http://localhost:8000` (UI + API).

---

## Permanent free host (you sign in once)

Push to GitHub, then create a **Web Service** on [Koyeb](https://www.koyeb.com) from the repo:

- Build: Docker (`Dockerfile` in repo root)
- Port: `8000`
- Env vars: same keys as `.env`
- Health check: `/health`

After deploy, one URL serves UI + API.
