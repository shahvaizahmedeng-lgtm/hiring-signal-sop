"""
normaliser.py

Turns a qualified candidate into a row matching the sourcing_companies
output schema exactly.

Drop-gate contract (from the SOP): this module PRODUCES
standardised_domain and company_name, so the normaliser gates on those
two and nothing else. company_linkedin_tag, geography, and company_size
are best-effort nullable fields; their absence never drops a row.
Everything module-specific lives in custom_fields JSONB.
"""

import logging

from domain_resolver import extract_linkedin_tag
from icp_parser import ParsedICP, to_country_code

logger = logging.getLogger(__name__)


class DropGate(Exception):
    """Raised when a record is missing a produced (gated) field."""


def normalise(candidate: dict, verdict: dict, domain: str | None,
              linkedin_url: str | None, icp: ParsedICP) -> dict:
    """
    Returns the insert-ready row dict, or raises DropGate with the reason.
    """
    if not domain:
        raise DropGate(f"no standardised_domain for {candidate.get('company_name')!r}")
    company_name = (candidate.get("company_name") or "").strip()
    if not company_name:
        raise DropGate(f"no company_name for job {candidate.get('job_id')!r}")

    # Geography: from the job location's country when we can infer it,
    # nullable when we cannot. Never guessed.
    geography = None
    location = candidate.get("location", "")
    if location:
        # 'Atlanta, GA' style locations are US; explicit country names map
        # through the code table. Anything ambiguous stays None.
        last_part = location.split(",")[-1].strip()
        geography = to_country_code(last_part)
        if geography is None and icp.geos and len(icp.geos) == 1:
            # Single-geo ICP and the job matched its search: safe inference.
            geography = to_country_code(icp.geos[0])

    return {
        "sourcing_config_id": icp.sourcing_config_id,
        "standardised_domain": domain,
        "company_name": company_name,
        "company_linkedin_tag": extract_linkedin_tag(linkedin_url or ""),
        "geography": geography,
        "company_size": verdict.get("estimated_size_band"),
        "custom_fields": {
            "job_id": candidate["job_id"],
            "job_title": candidate.get("title", ""),
            "job_location": location,
            "job_url": candidate.get("apply_url", ""),
            # The full posting text that justified this lead - the signal
            # itself, stored on the row per this scraper's contract.
            "job_description": (candidate.get("description") or "")[:8000],
            "qualification_confidence": verdict.get("confidence"),
            "qualification_reason": verdict.get("reason", ""),
            "linkedin_url": linkedin_url,
        },
    }
