"""
jd_fetcher.py

Fetches the FULL job description for a posting. This is the defining
feature of this scraper (#6: hiring signal WITH job description included):
the qualifier reasons over real posting text, not a name and a guess, and
the pushed row carries the signal that justified it.

Strategy (matches the SOP's job_scraper reference, which uses Jina.ai):
  1. Ask Jina Reader (https://r.jina.ai/<url>) for the clean text of the
     posting page. JINA_API_KEY is optional and only raises rate limits.
  2. On any failure - bad URL, timeout, rate limit, thin result - fall
     back to the description SerpAPI already returned. This step is a
     best-effort enrichment: it must never drop a candidate, because the
     SerpAPI description is itself a usable full JD.
"""

import logging
import time

import requests

from config import JINA_API_KEY

logger = logging.getLogger(__name__)

JINA_READER_BASE = "https://r.jina.ai/"
MAX_RETRIES = 2
TIMEOUT_SECONDS = 20
# Below this, treat the fetch as too thin to be a real JD and keep the
# SerpAPI fallback instead.
MIN_USABLE_LENGTH = 200


def _jina_headers() -> dict:
    headers = {"Accept": "text/plain"}
    if JINA_API_KEY:
        headers["Authorization"] = f"Bearer {JINA_API_KEY}"
    return headers


def fetch_full_description(url: str, fallback: str = "") -> str:
    """
    Return the fullest available job description text for a posting.

    `fallback` is the description SerpAPI already gave us; it is returned
    whenever the live fetch fails or comes back too thin. The result is
    always a string, never None, and this function never raises - a JD
    enrichment failure is not a reason to lose a lead.
    """
    fallback = (fallback or "").strip()

    if not url:
        return fallback

    target = f"{JINA_READER_BASE}{url}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(target, headers=_jina_headers(), timeout=TIMEOUT_SECONDS)
            if resp.status_code == 429:
                wait = 2 ** attempt
                logger.info("Jina rate limited, retry %s in %ss", attempt, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            text = (resp.text or "").strip()
            if len(text) >= MIN_USABLE_LENGTH and len(text) >= len(fallback):
                return text
            # Thin result: prefer whichever of the two has more signal.
            return text if len(text) > len(fallback) else fallback
        except requests.RequestException as e:
            logger.info("Jina fetch failed for %r (attempt %s, non-fatal): %s", url, attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(1)

    return fallback
