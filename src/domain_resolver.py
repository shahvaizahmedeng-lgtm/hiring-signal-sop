"""
domain_resolver.py

Resolves a company name to its standardised domain. Domain is a drop-gate
field in the output schema: no domain, no row. So this step tries harder
than usual, but when it genuinely cannot resolve a domain it returns None
and the normaliser drops the record with a logged reason, exactly as the
drop-gate contract expects.

Resolution order:
  1. module_inputs domain override (if the caller already knows it)
  2. SerpAPI Google search "<company> official website" and take the
     first organic result that is not a jobs board / social site
  3. Give up, return None

LinkedIn tags are best-effort and nullable by spec, so a missing tag
never blocks a row.
"""

import logging
import re
import time
from urllib.parse import urlparse

import requests

from config import SERPAPI_KEY

logger = logging.getLogger(__name__)

# Domains that are never a company's own website.
BLOCKED_DOMAINS = {
    "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com",
    "facebook.com", "twitter.com", "x.com", "instagram.com",
    "youtube.com", "wikipedia.org", "crunchbase.com", "bloomberg.com",
    "google.com", "monster.com", "simplyhired.com", "lever.co",
    "greenhouse.io", "workday.com", "myworkdayjobs.com", "bamboohr.com",
}


def standardise_domain(url_or_domain: str) -> str | None:
    """
    'https://www.Strategy-Group.ca/careers?x=1' -> 'strategy-group.ca'
    Spec: domain name only, no scheme, no www, no path.
    """
    if not url_or_domain:
        return None
    raw = url_or_domain.strip().lower()
    if "://" not in raw:
        raw = "https://" + raw
    try:
        host = urlparse(raw).netloc
    except ValueError:
        return None
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    # Basic sanity: must look like a domain.
    if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", host):
        return None
    return host


def extract_linkedin_tag(url: str) -> str | None:
    """
    'https://www.linkedin.com/company/strategysearchgroup/' -> 'strategysearchgroup'
    Spec: tag only, not the URL. Nullable, never gates a row.
    """
    if not url:
        return None
    match = re.search(r"linkedin\.com/company/([^/?#]+)", url, re.IGNORECASE)
    return match.group(1).strip().lower() if match else None


def resolve_domain(company_name: str, location: str = "") -> str | None:
    """Google-search the company and return the first plausible own-site domain."""
    if not company_name:
        return None

    params = {
        "engine": "google",
        "q": f'"{company_name}" official website {location}'.strip(),
        "api_key": SERPAPI_KEY,
        "num": 5,
    }
    try:
        resp = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        logger.warning("Domain resolution search failed for %r: %s", company_name, e)
        return None

    for result in data.get("organic_results", []) or []:
        link = result.get("link", "")
        domain = standardise_domain(link)
        if not domain:
            continue
        root = ".".join(domain.split(".")[-2:])
        if root in BLOCKED_DOMAINS or domain in BLOCKED_DOMAINS:
            continue
        return domain

    return None


def find_linkedin_url(company_name: str) -> str | None:
    """Best-effort LinkedIn company page lookup. Failure here is fine."""
    params = {
        "engine": "google",
        "q": f'site:linkedin.com/company "{company_name}"',
        "api_key": SERPAPI_KEY,
        "num": 3,
    }
    try:
        resp = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
        resp.raise_for_status()
        for result in resp.json().get("organic_results", []) or []:
            link = result.get("link", "")
            if "linkedin.com/company/" in link:
                return link
    except requests.RequestException as e:
        logger.info("LinkedIn lookup failed for %r (non-fatal): %s", company_name, e)
    return None
