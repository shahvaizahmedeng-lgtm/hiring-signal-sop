"""
qualifier.py

The AI qualification step. Takes a candidate company (with its job posting
context) and the parsed ICP config, asks the LLM one question: does this
company fit the ICP, yes or no, and why.

Uses OpenRouter so the model is swappable via env var without touching
code. The prompt demands strict JSON and the parser refuses anything
else: a qualification step that silently mis-parses is worse than one
that fails loudly, because bad rows poison the sourcing table downstream.
"""

import json
import logging
import time

import requests

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL
from icp_parser import ParsedICP

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
# Cap any single backoff so a heavily rate-limited free-tier provider makes
# a run fail fast (and visibly) instead of hanging for minutes per candidate.
MAX_BACKOFF_SECONDS = 8

QUALIFY_PROMPT = """You are a strict B2B lead qualifier.

Decide whether the company below fits the ICP. Respond with ONLY valid JSON,
no markdown fences, no commentary:

{{
  "qualified": true or false,
  "confidence": 0-100,
  "reason": "one sentence",
  "estimated_size_band": "one of: 1-10, 11-50, 51-200, 201-500, 501-1000, 1001-5000, 5001-10,000, 10,000+ or null"
}}

ICP CRITERIA:
- Market: {market}
- Target company sizes: {sizes}
- Target geographies: {geos}
- Hiring signals required: {signals}
- Qualifier: {qualifier}

COMPANY:
- Name: {company_name}
- Job posting title: {job_title}
- Job location: {location}
- Job description excerpt: {description}

Rules:
- The company must plausibly be the EMPLOYER hiring for the role, not a
  staffing agency or job board reposting it, unless the ICP explicitly
  targets agencies.
- If the description clearly contradicts the ICP (wrong industry, wrong
  geography), qualified is false.
- When information is genuinely insufficient, lean unqualified with low
  confidence rather than guessing qualified."""


class QualificationError(Exception):
    """Raised when the LLM step fails permanently for a candidate."""


def passes_confidence(verdict: dict, min_confidence: int) -> bool:
    """
    A verdict clears the gate only if it is qualified AND its confidence
    meets the caller's threshold. Kept as a pure function so the gating
    rule is unit-testable without standing up the whole pipeline.
    """
    if not verdict.get("qualified"):
        return False
    try:
        return int(verdict.get("confidence", 0)) >= int(min_confidence)
    except (TypeError, ValueError):
        return False


def qualify_company(candidate: dict, icp: ParsedICP) -> dict:
    """
    Returns {'qualified': bool, 'confidence': int, 'reason': str,
             'estimated_size_band': str|None}

    Raises QualificationError after exhausting retries so the caller can
    log the candidate as failed rather than defaulting it to qualified.
    """
    prompt = QUALIFY_PROMPT.format(
        market=icp.market_name or "not specified",
        sizes=", ".join(icp.company_sizes) or "any",
        geos=", ".join(icp.geos) or "any",
        signals=", ".join(icp.fixed_signals + icp.custom_signals) or "none",
        qualifier=icp.free_text_qualifier or "none",
        company_name=candidate.get("company_name", ""),
        job_title=candidate.get("title", ""),
        location=candidate.get("location", ""),
        # Full job description is this scraper's whole point: give the model
        # the real posting text (generously capped to protect the context
        # window) rather than a name-and-title guess.
        description=(candidate.get("description") or "")[:6000],
    )

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": OPENROUTER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(
                f"{OPENROUTER_BASE_URL}/chat/completions",
                headers=headers, json=body, timeout=60,
            )
            if resp.status_code == 429:
                # Honour the provider's Retry-After when given (free-tier
                # models are frequently rate-limited upstream), and record
                # it so an all-429 run fails with an honest reason instead
                # of a bare None.
                retry_after = resp.headers.get("Retry-After", "")
                raw_wait = int(retry_after) if retry_after.isdigit() else 2 ** attempt
                wait = min(raw_wait, MAX_BACKOFF_SECONDS)
                last_error = f"rate limited (HTTP 429), waited {wait}s"
                logger.warning("OpenRouter rate limited, retry %s in %ss", attempt, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            return _parse_verdict(content)
        except (requests.RequestException, KeyError, IndexError, ValueError) as e:
            last_error = e
            time.sleep(2 ** attempt)

    raise QualificationError(
        f"Qualification failed for {candidate.get('company_name')!r}: {last_error}"
    )


def _parse_verdict(content: str) -> dict:
    # Strip accidental markdown fences some models add despite instructions.
    cleaned = content.replace("```json", "").replace("```", "").strip()
    data = json.loads(cleaned)  # raises ValueError -> retried above
    return {
        "qualified": bool(data["qualified"]),
        "confidence": int(data.get("confidence", 0)),
        "reason": str(data.get("reason", "")),
        "estimated_size_band": data.get("estimated_size_band"),
    }
