"""
serp_client.py

Fetches Google Jobs results through SerpAPI. Every external call in this
module follows the same rule: retry with backoff on transient failures,
raise a clear error on permanent ones, never return half-parsed garbage.
"""

import logging
import time

import requests

from config import SERPAPI_KEY

logger = logging.getLogger(__name__)

SERPAPI_URL = "https://serpapi.com/search.json"
MAX_RETRIES = 3
BACKOFF_BASE_SECONDS = 2


class SerpError(Exception):
    """Raised when SerpAPI fails permanently for a query."""


def fetch_google_jobs(query: str, location_hint: str = "") -> list:
    """
    Returns a list of raw job dicts from Google Jobs for one query.

    Each dict keeps only what downstream steps need:
      job_id, title, company_name, location, description, apply_url

    `description` is the FULL job description SerpAPI returns for the
    posting (Google Jobs surfaces the complete text, not a snippet); the
    jd_fetcher may enrich it further via Jina. `apply_url` is the best
    link to the live posting, used as the enrichment target.
    """
    params = {
        "engine": "google_jobs",
        "q": query,
        "api_key": SERPAPI_KEY,
        "hl": "en",
    }
    if location_hint:
        params["location"] = location_hint

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(SERPAPI_URL, params=params, timeout=30)
            if resp.status_code == 429:
                # Rate limited: back off and retry.
                wait = BACKOFF_BASE_SECONDS ** attempt
                logger.warning("SerpAPI rate limited, retry %s in %ss", attempt, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            return _extract_jobs(data)
        except requests.RequestException as e:
            last_error = e
            wait = BACKOFF_BASE_SECONDS ** attempt
            logger.warning("SerpAPI error on attempt %s: %s. Retrying in %ss", attempt, e, wait)
            time.sleep(wait)

    raise SerpError(f"SerpAPI failed after {MAX_RETRIES} attempts for query {query!r}: {last_error}")


def _best_apply_url(item: dict) -> str:
    """Pick the most useful live link to the posting for JD enrichment."""
    apply_options = item.get("apply_options") or []
    for opt in apply_options:
        link = opt.get("link")
        if link:
            return link
    return item.get("share_link") or ""


def _extract_jobs(data: dict) -> list:
    jobs = []
    for item in data.get("jobs_results", []) or []:
        job_id = item.get("job_id")
        if not job_id:
            # No stable ID means no de-dupe key. Skip and log rather than
            # inventing an ID that breaks idempotency on re-runs.
            logger.info("Skipping job without job_id: %s", item.get("title"))
            continue
        jobs.append({
            "job_id": job_id,
            "title": item.get("title", ""),
            "company_name": item.get("company_name", ""),
            "location": item.get("location", ""),
            "description": (item.get("description") or "").strip(),
            "apply_url": _best_apply_url(item),
        })
    return jobs
