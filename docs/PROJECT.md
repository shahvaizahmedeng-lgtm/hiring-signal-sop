# Hiring Signal Sourcing Module — Project Documentation

**Scraper #6:** Hiring signal scraper **with full job description included**

Audition submission for the Sourcing Module SOP. Company-level, evergreen refresh.

---

## 1. What this module does

Receives the standard SOP payload (`default_inputs` + `module_inputs`), finds employers currently hiring for the segment’s signals (via Google Jobs), fetches the **full job description**, LLM-qualifies each employer against the ICP, normalises rows to the `sourcing_companies` schema, de-dupes on `custom_fields.job_id`, and inserts.

| Dimension | Value |
|-----------|--------|
| Granularity | Company |
| Refresh type | Evergreen (jobs refresh continuously) |
| Produced (drop-gate) | `standardised_domain`, `company_name` |
| De-dupe | `custom_fields.job_id` (scoped to `sourcing_config_id`) |
| App state | `backfill_done` |

---

## 2. Why this scraper

The sample ICP is healthcare providers with open Anesthesia/CRNA roles. Job postings are a strong evergreen signal: they refresh continuously, imply intent/budget, and the **description** gives the qualifier real text—not just a company name.

---

## 3. Architecture

```
POST /run
  → parse ICP (sourcing_config_id required)
  → build Google Jobs queries (signals × geos, or module_inputs override)
  → SerpAPI Google Jobs (retry / 429 backoff)
  → per candidate:
       full JD (Jina reader → SerpAPI fallback)
       LLM qualify vs ICP (OpenRouter, temp 0, JSON only)
       min_confidence gate
       resolve domain + LinkedIn (best-effort)
       normalise (drop-gate on domain + name)
       de-dupe on job_id → insert (or dry_run preview)
  → update state (backfill_done, last_search_queries)
  → 200 + run summary
```

### Module map

| File | Role |
|------|------|
| `src/main.py` | FastAPI app: `/`, `/health`, `/run`; pipeline orchestration |
| `src/icp_parser.py` | Parse SOP payload; derive search queries |
| `src/serp_client.py` | Google Jobs via SerpAPI |
| `src/jd_fetcher.py` | Full job description via Jina Reader |
| `src/qualifier.py` | OpenRouter ICP qualification + confidence gate |
| `src/domain_resolver.py` | Employer domain + LinkedIn tag |
| `src/normaliser.py` | Row shape + drop-gate |
| `src/supabase_client.py` | Insert + de-dupe; local JSON fallback if table missing |
| `src/state.py` | Per-config `backfill_done` / metadata |
| `src/config.py` | Env-based settings |
| `src/static/index.html` | Demo UI console |
| `sql/create_table.sql` | Temp `sourcing_companies` DDL for the audition |
| `docs/MODULE_DECLARATIONS.md` | Paste-ready `sourcing_modules` declarations |
| `tests/sample_input.json` | SOP sample payload |
| `tests/test_logic.py` | Pure-logic assertions (no network) |

---

## 4. API

### `GET /`

Demo UI — brand console to hit `/health` and `/run` in the browser.

### `GET /health`

```json
{
  "status": "ok",
  "store": {
    "ok": true,
    "backend": "supabase | local",
    "reason": "optional note"
  }
}
```

- `supabase` — writing to the real table  
- `local` — table missing / unreachable; using `local_sourcing_companies.json` so Live insert still demos

### `POST /run`

**Body:**

```json
{
  "default_inputs": {
    "sourcing_config_id": "...",
    "client_id": "...",
    "icp_config": { "market": {}, "segment": {}, "persona": {} }
  },
  "module_inputs": {
    "search_queries": ["optional override queries"],
    "min_confidence": 60,
    "dry_run": true
  }
}
```

**`module_inputs`:**

| Field | Default | Meaning |
|-------|---------|---------|
| `search_queries` | derived from signals × geos | Exact Google Jobs queries |
| `min_confidence` | `60` | Qualified but below this → `low_confidence` skip |
| `dry_run` | `false` | Full pipeline, no write; returns `would_insert` |

**Response (success shape):**

```json
{
  "status": "success | partial | insert_error | source_error",
  "mode": "backfill | incremental",
  "dry_run": false,
  "store": "local | supabase",
  "inserted": 2,
  "summary": {
    "candidates": 2,
    "inserted": 2,
    "duplicates_skipped": 0,
    "unqualified": 0,
    "low_confidence": 0,
    "drop_gated": 0,
    "qualification_failures": 0,
    "insert_failures": 0,
    "failed_queries": [],
    "skipped": [{ "job_id": "...", "stage": "...", "reason": "..." }]
  },
  "would_insert": [],
  "inserted_rows": []
}
```

HTTP **200** on completed runs (including partial failures), per SOP. **400** only when the payload cannot start work (e.g. missing `sourcing_config_id`, no queries).

Also: auto OpenAPI at `/docs`.

---

## 5. Output schema

Aligned with the SOP company table:

| Column | Status |
|--------|--------|
| `standardised_domain` | PRODUCED — drop-gate |
| `company_name` | PRODUCED — drop-gate |
| `company_linkedin_tag` | Best-effort, nullable |
| `geography` | ISO-2 when inferable, else null |
| `company_size` | LLM band estimate, nullable |
| `custom_fields` | JSONB (see below) |
| `sourcing_config_id` | From `default_inputs` |

**`custom_fields`:**

- `job_id` — de-dupe key  
- `job_title`, `job_location`, `job_url`  
- `job_description` — **full** posting text  
- `qualification_confidence`, `qualification_reason`  
- `linkedin_url` (full URL; tag lives in the column)

---

## 6. Design decisions (for the live review)

1. **Full JD is the product** — Jina Reader enrich + SerpAPI fallback; stored on the row; LLM qualifies on real posting text.  
2. **De-dupe on `job_id`, not domain** — one company can post many roles; each posting is a distinct signal.  
3. **Drop-gate only on produced fields** — domain + name; LinkedIn / geo / size never gate a row.  
4. **Idempotent re-runs** — de-dupe pre-check (+ unique index / local store); failed mid-run runs safely again.  
5. **Visible failures** — per-candidate skips with stage + reason in the summary.  
6. **Strict qualifier** — temp 0, JSON-only; staffing agencies rejected unless ICP targets them; insufficient info → unqualified.  
7. **State is minimal** — `backfill_done` (app); `last_search_queries` (metadata). Run timestamps stay at sourcing-config level.  
8. **Local store fallback** — audition works even before `sql/create_table.sql` is applied.

---

## 7. Setup & run

```bash
cd hiring-signal-module
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill keys
```

**Required env:**

| Variable | Purpose |
|----------|---------|
| `SUPABASE_URL` | Project URL |
| `SUPABASE_SERVICE_KEY` | Prefer **service_role**; publishable works with RLS off + grants on the temp table |
| `SERPAPI_KEY` | Google Jobs + domain search |
| `OPENROUTER_API_KEY` | LLM qualification |
| `OPENROUTER_MODEL` | Default `openai/gpt-oss-20b:free` (swappable) |
| `JINA_API_KEY` | Optional; raises Jina rate limits |
| `MAX_JOBS_PER_RUN` | Cap per query (default 20) |

Create the table (recommended for real Supabase inserts):

```bash
# Paste sql/create_table.sql into the Supabase SQL editor
```

Start:

```bash
uvicorn main:app --app-dir src --host 0.0.0.0 --port 8000
```

Open **http://localhost:8000** for the demo UI.

### CLI checks

```bash
curl http://localhost:8000/health

curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d @tests/sample_input.json

python3 tests/test_logic.py
```

---

## 8. Demo script (live review)

1. Open `http://localhost:8000` — health chip green; store chip shows `supabase` or `local JSON`.  
2. Preset **Healthcare-aligned** (market matches CRNA signal; bare SOP “PE Portfolio” market rejects hospitals).  
3. **Preview run** — dry-run; counters + expandable rows with full JDs.  
4. **Live insert** — writes to Supabase or local store; `inserted_rows` shown.  
5. **Re-run** — `duplicates_skipped` rises; `mode` becomes `incremental` after a successful backfill.  
6. Optional: raise `min_confidence`, set query override; show `/docs`.

**Note:** Free OpenRouter models can rate-limit under rapid batches. A short pause or a modest `MAX_JOBS_PER_RUN` keeps the demo smooth.

---

## 9. Declarations

Paste-ready description, input schema, output declaration, de-dupe JSON, and state schema:

→ [docs/MODULE_DECLARATIONS.md](MODULE_DECLARATIONS.md)

---

## 10. Tests

```bash
python3 tests/test_logic.py
```

Covers: ICP parsing (SOP sample), query building, domain / LinkedIn helpers, drop-gate, country codes, `min_confidence`, JD fetch fallback (mocked). No live network required.

---

## 11. Operational notes

- **Backfill vs incremental** — first clean backfill (`inserted > 0`, no failed queries) sets `backfill_done`; later runs use a reduced per-query depth.  
- **Local store file** — `local_sourcing_companies.json` (gitignored). Safe for audition demos; switch to real inserts after running the SQL.  
- **Secrets** — never commit `.env`. Use `.env.example` as the template.

---

*Hiring Signal · Sourcing Module SOP audition · EOD 14 Jul 2026 BST*
