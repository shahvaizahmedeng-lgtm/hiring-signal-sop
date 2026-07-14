# Module Declarations: hiring_signal_scraper

These are the declarations for the `sourcing_modules` table row, in the
format from the SOP.

---

## Description

```
hiring_signal_scraper: Scrapes Google Jobs (via SerpAPI) using search
queries derived from the segment's fixed_signals and custom_signals,
one query per signal per geo. For each posting it fetches the FULL job
description (Jina.ai reader, with the SerpAPI description as fallback),
so qualification reasons over real posting text rather than a name and a
title. Each posting's company is resolved to its domain via targeted
Google search, then qualified against the full ICP config (market, size
bands, geos, free_text_qualifier) by an LLM (OpenRouter, model swappable
via env) that returns strict JSON. Employer check included: staffing
agencies reposting roles are rejected unless the ICP targets agencies. A
tunable min_confidence gate holds back low-confidence matches. Qualified
companies are normalised and pushed to sourcing_companies with the full
job description stored in custom_fields.
```

## Granularity, Segment Level, Refresh Type

- Granularity: **company** (as required)
- Segment level: taken from the payload
- Refresh type: **evergreen**. Job postings are a continuously refreshing
  signal; re-running the module surfaces new postings while the de-dupe
  silently skips everything already pushed. `backfill_done` switches the
  module from full backfill to incremental fetching.

## Input Schema

```json
[
  {
    "name": "search_queries",
    "type": "array of strings",
    "required": false,
    "description": "Optional override. When provided, these exact queries are sent to Google Jobs instead of deriving queries from the segment signals. Use when an operator knows precise search phrasing that outperforms the derived queries.",
    "interface_suggestion": "Free-text tag input on the sourcing config. An AI assist button could propose queries from the segment signals for the operator to edit before saving."
  },
  {
    "name": "min_confidence",
    "type": "integer (0-100)",
    "required": false,
    "default": 60,
    "description": "Minimum LLM qualification confidence for a company to be pushed. Qualified verdicts below this are held back and reported as low_confidence in the run summary. Raise for stricter lists, lower for wider coverage.",
    "interface_suggestion": "Slider on the sourcing config, default 60."
  },
  {
    "name": "dry_run",
    "type": "boolean",
    "required": false,
    "default": false,
    "description": "When true, runs the full pipeline (fetch, JD enrichment, qualify, normalise, drop-gate) but skips the database write and returns the rows that WOULD be inserted under a would_insert key. Used to preview a run or demo it without touching the sourcing table.",
    "interface_suggestion": "A 'Preview run' button next to the trigger, distinct from the live run."
  }
]
```

The input schema can be left effectively empty: with no `module_inputs`
the module derives everything from `default_inputs` alone, per the SOP Q&A.

## Output Declaration

| Output field | Source | Status |
|---|---|---|
| standardised_domain | Google search resolution of the posting's employer | PRODUCED - drop-gate |
| company_name | Google Jobs `company_name` | PRODUCED - drop-gate |
| company_linkedin_tag | Targeted `site:linkedin.com/company` search | BEST-EFFORT - nullable, not gated |
| geography | Job location country (ISO-2), single-geo ICP inference fallback | Optional standard - nullable, never guessed |
| company_size | LLM-estimated band from posting context | Optional standard - estimate, not a precise figure |

Custom fields (JSONB), stored per row:

```json
{
  "job_id": "Google Jobs stable ID (de-dupe key)",
  "job_title": "posting title",
  "job_location": "raw location string",
  "job_url": "apply/share link to the live posting",
  "job_description": "full job description (Jina fetch, SerpAPI fallback)",
  "qualification_confidence": "0-100 from the LLM verdict",
  "qualification_reason": "one-sentence LLM reasoning",
  "linkedin_url": "full URL when found (tag column holds the tag only)"
}
```

## De-dupe JSON

```json
[
  { "field": "custom_fields.job_id" }
]
```

Reasoning: one company legitimately appears across many postings, so
domain-level de-dupe would wrongly drop new hiring signals from companies
already surfaced. The posting itself is the unit of novelty, so `job_id`
is the key, scoped to the sourcing config. Duplicate found = silent skip,
no insert, no update, no error.

## State Schema

```json
{
  "state_schema": [
    {
      "name": "backfill_done",
      "type": "boolean",
      "schema_type": "app",
      "default": false,
      "description": "Backfills all current job postings for the derived queries, then stores whether that backfill happened. false on first run; set to true only after a backfill run completes with at least one insert and zero failed queries. Subsequent runs read this and fetch incrementally (reduced per-query depth).",
      "read_recommendation": "Display as a badge on the sourcing config (Backfilled / Not backfilled). Logically, check before each run: if false, run full backfill; if true, run incremental."
    },
    {
      "name": "last_search_queries",
      "type": "array of strings",
      "schema_type": "metadata",
      "default": null,
      "description": "The exact queries sent to SerpAPI on the last run. Write-only, for debugging what was searched when a run surfaces unexpected results."
    }
  ]
}
```

State values live on the module server (JSON file keyed by
sourcing_config_id for this test; same interface would back onto a table
in production). Declarations here describe them; the server owns them.
