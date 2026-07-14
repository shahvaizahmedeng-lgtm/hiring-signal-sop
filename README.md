# Hiring Signal Sourcing Module

A sourcing module per the SOP: receives the standard `default_inputs` +
`module_inputs` POST, scrapes Google Jobs for the segment's hiring
signals, LLM-qualifies each employer against the full ICP config, and
pushes normalised company rows directly into `sourcing_companies`.

Built for the audition task. Scraper #6: hiring signal scraper with job
description included.

**Full project documentation:** [docs/PROJECT.md](docs/PROJECT.md)

---

## Why This Scraper

The provided sample ICP is literally this use case: healthcare providers
with open Anesthesia/CRNA roles. Job postings are also the strongest
evergreen signal on the list: they refresh continuously, they carry
intent (a company hiring for X has budget and pain around X right now),
and the posting description gives the qualifier real text to reason
over instead of guessing from a company name.

## Pipeline

```
POST /run
  └─ parse default_inputs (fail loudly if sourcing_config_id missing)
  └─ build queries from fixed_signals x geos
     (module_inputs.search_queries overrides)
  └─ SerpAPI Google Jobs per query (retry + backoff, 429-aware)
  └─ per candidate:
       fetch FULL job description (Jina reader, SerpAPI fallback)
       LLM-qualify vs ICP on the real posting text (OpenRouter, JSON, temp 0)
       min_confidence gate (tunable; low-confidence held back, not dropped)
       resolve employer domain (Google search, jobs boards blocklisted)
       normalise -> drop-gate on domain + company_name only
       de-dupe on custom_fields.job_id (scoped to sourcing config)
       insert to sourcing_companies (or preview only, if dry_run)
  └─ update state (backfill_done, last_search_queries)
  └─ 200 + run summary (inserted / skipped / failed, with reasons)
```

## Design Decisions (the short version)

**The full job description is the product, not a snippet.** This is scraper
#6: "hiring signal WITH job description included." For every posting the
module fetches the full description via Jina's reader (falling back to the
complete text SerpAPI already returns), then qualifies on that real text
and stores it in `custom_fields.job_description`. The qualifier reasons
over what the role actually asks for, not a company name and a title, and
the pushed row carries the evidence that justified it.

**De-dupe on `job_id`, not domain.** A company posting five CRNA roles is
five signals from one company. Domain de-dupe would suppress four of
them. The posting is the unit of novelty; the SOP's own job-posting
example uses the same key.

**Drop-gate only on what this module produces.** `standardised_domain` +
`company_name`, exactly per the output declaration. LinkedIn tag,
geography, and size are nullable best-effort; their absence never costs
a row.

**Idempotent by construction.** The SOP says assume a run can fail
partway and be triggered again. Because every insert passes the de-dupe
pre-check (plus a unique index in Postgres to close the concurrent-run
race), re-triggering a half-finished run simply skips what already
landed and completes the rest. No cleanup step, no duplicate rows.

**Failures are visible, not silent.** Per-candidate failures (LLM parse
error, unresolvable domain, insert error) are counted and returned in
the run summary with reasons, and logged with structure. A skipped lead
you can see is a fixable problem; a silently dropped one is not.

**The qualifier is strict on purpose.** Temperature 0, JSON-only
response contract, and a refuse-to-guess rule: insufficient information
leans unqualified with low confidence. It also rejects staffing agencies
reposting roles unless the ICP targets agencies, because the employer is
the lead, not the middleman. A polluted sourcing table costs more than a
conservative one.

**Geography is inferred, never guessed.** Country parsed from the job
location; when ambiguous and the ICP has exactly one geo, that geo is a
safe inference (the posting matched a geo-scoped search). Otherwise
NULL, as the schema allows.

**State is isolated behind two functions.** `get_state` / `update_state`
back onto a JSON file for the test and would back onto a table in
production without touching any pipeline code. `backfill_done` flips
only after a clean backfill (inserts > 0, zero failed queries), so a
partially failed first run stays in backfill mode and re-fetches
properly next trigger.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in the four keys
# create the table: run sql/create_table.sql in the Supabase SQL editor
uvicorn main:app --app-dir src --host 0.0.0.0 --port 8000
```

Required keys in `.env`:

| Var | Where to get it |
|---|---|
| SUPABASE_URL, SUPABASE_SERVICE_KEY | Supabase project settings (service key scoped for the test) |
| SERPAPI_KEY | serpapi.com free tier (100 searches/mo) |
| OPENROUTER_API_KEY | openrouter.ai (free-tier model set by default) |
| JINA_API_KEY | optional; the full-JD fetch works keyless, a key raises Jina rate limits |

## Demo

```bash
# health
curl http://localhost:8000/health

# run with the SOP's exact sample input
curl -X POST http://localhost:8000/run \
  -H "Content-Type: application/json" \
  -d @tests/sample_input.json
```

Expected response shape:

```json
{
  "status": "success",
  "mode": "backfill",
  "dry_run": false,
  "inserted": 7,
  "summary": {
    "candidates": 20, "inserted": 7, "duplicates_skipped": 0,
    "unqualified": 9, "low_confidence": 0, "drop_gated": 3,
    "qualification_failures": 1, "insert_failures": 0, "failed_queries": [],
    "skipped": [ {"job_id": "...", "stage": "qualify", "reason": "unqualified: staffing agency"} ]
  }
}
```

Run it twice: the second run reports `duplicates_skipped` where the first
reported `inserted`, and `mode` flips to `incremental`. That is the
de-dupe and state machinery working.

### Optional module_inputs

```json
{
  "module_inputs": {
    "search_queries": ["Anesthesia CRNA jobs United States"],
    "min_confidence": 70,
    "dry_run": true
  }
}
```

- `search_queries` - exact queries to send instead of deriving from signals.
- `min_confidence` (default 60) - qualified verdicts below this are held
  back and reported under `low_confidence`.
- `dry_run` - run the full pipeline but skip the write; the response adds a
  `would_insert` array of the rows that would have landed. Useful to preview
  or demo a run without touching the sourcing table.

## Tests

```bash
python3 tests/test_logic.py
```

38 assertions over the pure logic, run against the SOP's exact sample
input: parsing, query derivation, domain standardisation, LinkedIn tag
extraction, country codes, normalisation, both drop-gate paths, the
min_confidence gate, and the full-JD fetch fallback behaviour.

## Module Declarations

Registration values (description, input schema, output declaration,
de-dupe JSON, state schema) are in
[docs/MODULE_DECLARATIONS.md](docs/MODULE_DECLARATIONS.md), in the SOP's
format, ready to paste into the `sourcing_modules` row.
