# Deploy to Fly.io: Hiring Signal Sourcing Module

## What kind of app is this?

| Question | Answer |
|----------|--------|
| Framework | **FastAPI** (Python) |
| Runtime | **Long-lived HTTP server** via **uvicorn** |
| Type | Backend **REST API** + static **demo UI** (`GET /`) |
| Not | Not serverless, not Next.js, not a static site only |

Clients call:

- `GET /` — demo console  
- `GET /health` — health + store backend  
- `POST /run` — full sourcing pipeline (can take 30–90s)

That is why Fly.io / Koyeb / a VPS fit, and Vercel does not.

---

## Prerequisites

1. [Fly.io account](https://fly.io/signup) (free allowance)  
2. Install CLI:

```bash
# macOS
brew install flyctl

# or
curl -L https://fly.io/install.sh | sh
```

3. Log in:

```bash
fly auth login
```

4. Repo on your machine with this `Dockerfile` + `fly.toml`.

---

## Deploy steps

### 1. Create / link the app (first time)

From the project root:

```bash
cd hiring-signal-module

# Uses existing fly.toml (app name: hiring-signal-sop). Change app name in fly.toml if taken.
fly launch --copy-config --no-deploy
```

If the app name `hiring-signal-sop` is taken, edit `app = '...'` in [`fly.toml`](../fly.toml) to something unique (e.g. `hiring-signal-yourname`).

### 2. Set secrets (your API keys)

Never commit `.env`. Push secrets to Fly:

```bash
fly secrets set \
  SUPABASE_URL="https://YOUR_PROJECT.supabase.co" \
  SUPABASE_SERVICE_KEY="YOUR_KEY" \
  SERPAPI_KEY="YOUR_KEY" \
  OPENROUTER_API_KEY="YOUR_KEY" \
  OPENROUTER_MODEL="openai/gpt-oss-20b:free"
```

Optional:

```bash
fly secrets set JINA_API_KEY="YOUR_KEY"
fly secrets set MAX_JOBS_PER_RUN="5"
```

### 3. Deploy

```bash
fly deploy
```

### 4. Open it

```bash
fly open
# or
fly status
```

URL will look like:

`https://hiring-signal-sop.fly.dev`

Check:

```bash
curl https://hiring-signal-sop.fly.dev/health
```

---

## Useful commands

```bash
fly logs          # live logs (watch a /run)
fly status
fly ssh console   # shell into the machine
fly secrets list
fly scale memory 512
```

---

## Free-tier notes

- Free allowance can **auto-stop** machines when idle (`auto_stop_machines` in `fly.toml`). First request after sleep wakes the VM (~10–30s).  
- Hit `/health` once before a live demo so it’s warm.  
- Disk is ephemeral: local JSON store resets on redeploy — prefer Supabase table for durable rows (`sql/create_table.sql`).  
- Keep `MAX_JOBS_PER_RUN=5` so `/run` stays within free machine limits.

---

## If deploy fails

| Problem | Fix |
|---------|-----|
| App name taken | Change `app` in `fly.toml` |
| Health check fail | Ensure `/health` returns 200; check `fly logs` |
| Out of free allowance | Wait for monthly reset, or scale to 0 / destroy spare apps: `fly apps destroy OTHER_APP` |
| Secrets missing | Re-run `fly secrets set ...` then `fly deploy` |
