"""
supabase_client.py

Inserts normalised rows into sourcing_companies with the de-dupe contract
from the SOP: duplicate found -> silently skipped, no insert, no update,
no error. De-dupe key for this module is custom_fields.job_id, scoped to
the sourcing_config_id.

When the Supabase table is missing (common during the audition before
sql/create_table.sql is run), inserts transparently fall back to a local
JSON store so Live insert / Re-run / de-dupe still demo end-to-end.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from threading import Lock

import requests

from config import STATE_FILE, SUPABASE_SERVICE_KEY, SUPABASE_URL

logger = logging.getLogger(__name__)

_lock = Lock()
_use_local: bool | None = None  # None = unknown, True/False once probed
LOCAL_STORE_FILE = os.environ.get(
    "LOCAL_COMPANIES_FILE",
    os.path.join(os.path.dirname(STATE_FILE) if os.path.dirname(STATE_FILE) else ".", "local_sourcing_companies.json"),
)


class SupabaseError(Exception):
    pass


def _headers():
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


def _is_missing_table(resp: requests.Response) -> bool:
    if resp.status_code != 404:
        return False
    text = resp.text or ""
    return "PGRST205" in text or "sourcing_companies" in text


def _local_load() -> list:
    if not os.path.exists(LOCAL_STORE_FILE):
        return []
    try:
        with open(LOCAL_STORE_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Local store unreadable (%s); starting fresh", e)
        return []


def _local_save(rows: list) -> None:
    tmp = LOCAL_STORE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rows, f, indent=2)
    os.replace(tmp, LOCAL_STORE_FILE)


def _local_is_duplicate(sourcing_config_id: str, job_id: str) -> bool:
    with _lock:
        for row in _local_load():
            if row.get("sourcing_config_id") != sourcing_config_id:
                continue
            cf = row.get("custom_fields") or {}
            if cf.get("job_id") == job_id:
                return True
        return False


def _local_insert(row: dict) -> bool:
    job_id = row["custom_fields"]["job_id"]
    if _local_is_duplicate(row["sourcing_config_id"], job_id):
        logger.info("[local] Duplicate skipped (job_id=%s)", job_id)
        return False
    with _lock:
        rows = _local_load()
        # Race-safe re-check under lock
        for existing in rows:
            if (
                existing.get("sourcing_config_id") == row["sourcing_config_id"]
                and (existing.get("custom_fields") or {}).get("job_id") == job_id
            ):
                logger.info("[local] Duplicate skipped under lock (job_id=%s)", job_id)
                return False
        stored = {
            **row,
            "id": str(uuid.uuid4()),
            "surfaced_at": datetime.now(timezone.utc).isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "_store": "local",
        }
        rows.append(stored)
        _local_save(rows)
    logger.info(
        "[local] Inserted %s (%s) — Supabase table missing; using %s",
        row["company_name"], row["standardised_domain"], LOCAL_STORE_FILE,
    )
    return True


def store_backend() -> str:
    """Return 'supabase', 'local', or 'unknown' for health / UI."""
    if _use_local is True:
        return "local"
    if _use_local is False:
        return "supabase"
    return "unknown"


def probe_store() -> dict:
    """
    Lightweight check for the demo UI: is sourcing_companies reachable?
    Does not force the local fallback permanently — that happens on first
    failed write probe in is_duplicate / insert_company.
    """
    global _use_local
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        _use_local = True
        return {"ok": True, "backend": "local", "reason": "missing SUPABASE_URL or key"}

    if _use_local is True:
        return {"ok": True, "backend": "local", "reason": "fallback after missing table", "path": LOCAL_STORE_FILE}

    try:
        url = f"{SUPABASE_URL}/rest/v1/sourcing_companies"
        resp = requests.get(
            url,
            headers=_headers(),
            params={"select": "id", "limit": "1"},
            timeout=10,
        )
        if resp.status_code == 200:
            _use_local = False
            return {"ok": True, "backend": "supabase"}
        if _is_missing_table(resp):
            _use_local = True
            return {
                "ok": True,
                "backend": "local",
                "reason": "sourcing_companies table missing — using local JSON store",
                "path": LOCAL_STORE_FILE,
            }
        return {"ok": False, "backend": "supabase", "reason": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except requests.RequestException as e:
        _use_local = True
        return {"ok": True, "backend": "local", "reason": f"supabase unreachable ({e}); local store", "path": LOCAL_STORE_FILE}


def is_duplicate(sourcing_config_id: str, job_id: str) -> bool:
    """De-dupe check: same sourcing_config_id + same custom_fields.job_id."""
    global _use_local
    if _use_local is True:
        return _local_is_duplicate(sourcing_config_id, job_id)

    url = f"{SUPABASE_URL}/rest/v1/sourcing_companies"
    params = {
        "select": "id",
        "sourcing_config_id": f"eq.{sourcing_config_id}",
        "custom_fields->>job_id": f"eq.{job_id}",
        "limit": "1",
    }
    resp = requests.get(url, headers=_headers(), params=params, timeout=30)
    if resp.status_code == 200:
        _use_local = False
        return len(resp.json()) > 0
    if _is_missing_table(resp):
        logger.warning("sourcing_companies missing — switching to local JSON store")
        _use_local = True
        return _local_is_duplicate(sourcing_config_id, job_id)
    raise SupabaseError(f"De-dupe check failed: {resp.status_code} {resp.text[:300]}")


def insert_company(row: dict) -> bool:
    """
    Inserts one row. Returns True on insert, False on silent duplicate skip.
    """
    global _use_local
    job_id = row["custom_fields"]["job_id"]

    if _use_local is True:
        return _local_insert(row)

    if is_duplicate(row["sourcing_config_id"], job_id):
        logger.info("Duplicate skipped (job_id=%s, domain=%s)", job_id, row["standardised_domain"])
        return False

    # is_duplicate may have flipped us to local
    if _use_local is True:
        return _local_insert(row)

    url = f"{SUPABASE_URL}/rest/v1/sourcing_companies"
    resp = requests.post(url, headers=_headers(), json=row, timeout=30)

    if resp.status_code in (200, 201):
        logger.info("Inserted %s (%s)", row["company_name"], row["standardised_domain"])
        return True
    if resp.status_code == 409:
        logger.info("Duplicate caught by unique index, skipped (job_id=%s)", job_id)
        return False
    if _is_missing_table(resp):
        logger.warning("sourcing_companies missing on insert — switching to local store")
        _use_local = True
        return _local_insert(row)

    raise SupabaseError(f"Insert failed: {resp.status_code} {resp.text[:300]}")
