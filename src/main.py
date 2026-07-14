"""
main.py

The sourcing module endpoint. Receives the single JSON POST described in
the SOP (default_inputs + module_inputs), runs the pipeline, pushes rows
directly into sourcing_companies, returns 200 with a run summary.

Pipeline per run:
  parse ICP -> build queries -> fetch Google Jobs (SerpAPI)
  -> per candidate: resolve domain -> LLM-qualify against ICP
  -> normalise (drop-gate) -> de-dupe -> insert

Failure philosophy (matches the SOP's 'assume a run can fail partway and
be triggered again'):
  - Per-candidate failures are logged and counted, never fatal to the run.
  - The de-dupe pre-check makes re-runs idempotent: anything already
    pushed is silently skipped on the next trigger.
  - Only a failure before any work starts (bad payload, no queries)
    returns a 4xx.

Run:  uvicorn main:app --host 0.0.0.0 --port 8000
"""

import logging
import os
import sys

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Any

sys.path.insert(0, ".")

from config import MAX_JOBS_PER_RUN, DEFAULT_MIN_CONFIDENCE
from icp_parser import parse_default_inputs, build_search_queries
from serp_client import fetch_google_jobs, SerpError
from jd_fetcher import fetch_full_description
from domain_resolver import resolve_domain, find_linkedin_url
from qualifier import qualify_company, passes_confidence, QualificationError
from normaliser import normalise, DropGate
from supabase_client import insert_company, SupabaseError, probe_store, store_backend
from state import get_state, update_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("sourcing_module")

app = FastAPI(title="Hiring Signal Sourcing Module")

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


class SourcingPayload(BaseModel):
    default_inputs: dict
    module_inputs: dict | None = None


@app.get("/")
def demo_ui():
    """Interview demo console — exercises /health and /run in the browser."""
    index = os.path.join(_STATIC_DIR, "index.html")
    if not os.path.isfile(index):
        raise HTTPException(status_code=404, detail="Demo UI not found")
    return FileResponse(index)


@app.get("/health")
def health():
    store = probe_store()
    return {
        "status": "ok",
        "store": store,
    }


@app.post("/run")
def run_module(payload: SourcingPayload) -> Any:
    # ---- Parse (fail loudly at the door) ----
    try:
        icp = parse_default_inputs(payload.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    module_inputs = payload.module_inputs or {}

    queries = build_search_queries(icp, module_inputs)
    if not queries:
        raise HTTPException(
            status_code=400,
            detail="No search queries derivable: segment has no fixed/custom "
                   "signals and module_inputs.search_queries was not provided.",
        )

    # Runtime knobs declared in the input schema.
    try:
        min_confidence = int(module_inputs.get("min_confidence", DEFAULT_MIN_CONFIDENCE))
    except (TypeError, ValueError):
        min_confidence = DEFAULT_MIN_CONFIDENCE
    min_confidence = max(0, min(100, min_confidence))
    dry_run = bool(module_inputs.get("dry_run", False))

    run_state = get_state(icp.sourcing_config_id)
    is_backfill = not run_state.get("backfill_done", False)
    # Incremental uses half depth; never exceeds MAX. Floor of 5 only when
    # MAX itself is large enough (otherwise MAX=2 wrongly became 5).
    if is_backfill:
        per_query_cap = MAX_JOBS_PER_RUN
    else:
        half = max(MAX_JOBS_PER_RUN // 2, 1)
        floor = min(5, MAX_JOBS_PER_RUN) if MAX_JOBS_PER_RUN >= 5 else half
        per_query_cap = min(MAX_JOBS_PER_RUN, max(half, floor))

    logger.info(
        "Run start | config=%s | mode=%s | %d queries | min_confidence=%d | dry_run=%s",
        icp.sourcing_config_id, "backfill" if is_backfill else "incremental",
        len(queries), min_confidence, dry_run,
    )

    # ---- Fetch candidates ----
    candidates, failed_queries = [], []
    for q in queries:
        try:
            jobs = fetch_google_jobs(q)
            candidates.extend(jobs[:per_query_cap])
            logger.info("Query %r -> %d jobs", q, len(jobs))
        except SerpError as e:
            logger.error("Query failed permanently: %s", e)
            failed_queries.append(q)

    if not candidates and failed_queries:
        # Everything failed upstream: report it, still 200 per spec, but
        # the summary makes the failure visible instead of silent.
        return JSONResponse({
            "status": "source_error",
            "inserted": 0,
            "summary": {"failed_queries": failed_queries},
        })

    # ---- Qualify, normalise, insert ----
    counters = {
        "candidates": len(candidates), "inserted": 0, "duplicates_skipped": 0,
        "unqualified": 0, "low_confidence": 0, "drop_gated": 0,
        "qualification_failures": 0, "insert_failures": 0,
    }
    skipped_log = []
    would_insert = []  # populated only in dry_run mode
    inserted_rows = []
    duplicate_rows = []
    # Full lead ledger for the demo UI (every processed outcome with context).
    ledger = []

    for cand in candidates:
        # ---- Enrich with the full job description (the point of this
        # scraper): fetch the live posting text, fall back to SerpAPI's
        # description. Best-effort, never fatal. ----
        cand["description"] = fetch_full_description(
            cand.get("apply_url", ""), cand.get("description", "")
        )

        def _skip(stage: str, reason: str, extra: dict | None = None):
            entry = {
                "job_id": cand["job_id"],
                "company_name": cand.get("company_name") or "",
                "job_title": cand.get("title") or "",
                "stage": stage,
                "reason": reason,
            }
            if extra:
                entry.update(extra)
            skipped_log.append(entry)
            ledger.append({"outcome": "skipped", **entry})

        try:
            verdict = qualify_company(cand, icp)
        except QualificationError as e:
            counters["qualification_failures"] += 1
            _skip("qualify", str(e)[:200])
            continue

        if not verdict["qualified"]:
            counters["unqualified"] += 1
            _skip(
                "qualify",
                f"unqualified: {verdict['reason']}",
                {"confidence": verdict.get("confidence")},
            )
            continue

        # Confidence gate: qualified but below the caller's threshold is
        # held back, and shown as such so the threshold is tunable.
        if not passes_confidence(verdict, min_confidence):
            counters["low_confidence"] += 1
            _skip(
                "confidence_gate",
                f"confidence {verdict['confidence']} < min_confidence {min_confidence}",
                {"confidence": verdict.get("confidence")},
            )
            continue

        domain = resolve_domain(cand.get("company_name", ""), cand.get("location", ""))
        linkedin_url = find_linkedin_url(cand.get("company_name", "")) if domain else None

        try:
            row = normalise(cand, verdict, domain, linkedin_url, icp)
        except DropGate as e:
            counters["drop_gated"] += 1
            _skip("drop_gate", str(e))
            continue

        if dry_run:
            # Full pipeline, no write: report exactly what would land.
            counters["inserted"] += 1
            would_insert.append(row)
            ledger.append({
                "outcome": "would_insert",
                "company_name": row["company_name"],
                "job_id": row["custom_fields"]["job_id"],
                "row": row,
            })
            logger.info("[dry_run] would insert %s (%s)", row["company_name"], row["standardised_domain"])
            continue

        try:
            if insert_company(row):
                counters["inserted"] += 1
                inserted_rows.append(row)
                ledger.append({
                    "outcome": "inserted",
                    "company_name": row["company_name"],
                    "job_id": row["custom_fields"]["job_id"],
                    "row": row,
                })
            else:
                counters["duplicates_skipped"] += 1
                duplicate_rows.append(row)
                ledger.append({
                    "outcome": "duplicate",
                    "company_name": row["company_name"],
                    "job_id": row["custom_fields"]["job_id"],
                    "row": row,
                    "stage": "dedupe",
                    "reason": "already in store for this sourcing_config_id + job_id",
                })
        except SupabaseError as e:
            counters["insert_failures"] += 1
            _skip("insert", str(e)[:200])

    # ---- State update ----
    # A dry run is a preview: it must not advance state or claim a backfill.
    if not dry_run:
        if is_backfill and counters["inserted"] > 0 and not failed_queries:
            update_state(icp.sourcing_config_id, backfill_done=True)
            logger.info("Backfill complete for %s", icp.sourcing_config_id)
        update_state(icp.sourcing_config_id, last_search_queries=queries)

    # Honest run status for the demo UI (SOP still returns HTTP 200).
    if counters["insert_failures"] and counters["inserted"] == 0 and counters["candidates"] > 0:
        run_status = "insert_error"
    elif counters["insert_failures"] or counters["qualification_failures"] or failed_queries:
        run_status = "partial"
    else:
        run_status = "success"

    # Showcase payload: every normalised company with its outcome for the UI.
    showcase = [
        {"outcome": e["outcome"], "row": e["row"]}
        for e in ledger
        if e.get("row")
    ]

    logger.info("Run done | dry_run=%s | status=%s | store=%s | %s", dry_run, run_status, store_backend(), counters)
    response = {
        "status": run_status,
        "mode": "backfill" if is_backfill else "incremental",
        "dry_run": dry_run,
        "store": store_backend(),
        "queries": queries,
        "inserted": counters["inserted"],
        "summary": {**counters, "failed_queries": failed_queries, "skipped": skipped_log[:50]},
        "ledger": ledger[:50],
        "companies": showcase,
    }
    if dry_run:
        response["would_insert"] = would_insert
    if inserted_rows:
        response["inserted_rows"] = inserted_rows
    if duplicate_rows:
        response["duplicate_rows"] = duplicate_rows
    return JSONResponse(response)
