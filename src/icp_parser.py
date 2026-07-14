"""
icp_parser.py

Parses the default_inputs payload (their exact format from the SOP) into
the values this module needs: search signals, geos, size bands, and the
free-text qualifier used by the LLM qualification step.

Designed defensively: segment fields sometimes arrive as JSON-encoded
strings (as seen in their raw table dumps) and sometimes as real arrays.
This handles both without caring which one shows up.
"""

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedICP:
    sourcing_config_id: str
    client_id: str
    market_name: str
    fixed_signals: list = field(default_factory=list)
    custom_signals: list = field(default_factory=list)
    geos: list = field(default_factory=list)
    company_sizes: list = field(default_factory=list)
    free_text_qualifier: str = ""
    job_titles: list = field(default_factory=list)


def _as_list(value: Any) -> list:
    """Segment fields arrive either as lists or as JSON-encoded strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else [parsed]
        except (json.JSONDecodeError, TypeError):
            return [value] if value else []
    return [value]


# Country name -> ISO-2 code for the geography output column.
COUNTRY_CODES = {
    "united states": "US", "usa": "US", "us": "US",
    "united kingdom": "GB", "uk": "GB",
    "canada": "CA", "germany": "DE", "france": "FR",
    "australia": "AU", "netherlands": "NL", "spain": "ES",
    "italy": "IT", "sweden": "SE", "switzerland": "CH",
    "ireland": "IE", "belgium": "BE", "austria": "AT",
    "denmark": "DK", "norway": "NO", "finland": "FI",
    "poland": "PL", "portugal": "PT", "china": "CN",
    "india": "IN", "japan": "JP", "singapore": "SG",
    "united arab emirates": "AE", "uae": "AE",
    "brazil": "BR", "mexico": "MX",
}


def to_country_code(geo: str) -> str | None:
    """Best-effort country name to ISO-2 code. Returns None when unknown
    rather than guessing, because geography is nullable by spec."""
    if not geo:
        return None
    key = geo.strip().lower()
    if len(key) == 2 and key.upper() in COUNTRY_CODES.values():
        return key.upper()
    return COUNTRY_CODES.get(key)


def parse_default_inputs(payload: dict) -> ParsedICP:
    """
    Accepts the full POST body and returns a ParsedICP.

    Raises ValueError with a clear message when required identifiers are
    missing, because a run without a sourcing_config_id cannot write rows
    and should fail loudly at the door, not partway through.
    """
    defaults = payload.get("default_inputs") or {}

    sourcing_config_id = defaults.get("sourcing_config_id")
    if not sourcing_config_id:
        raise ValueError("default_inputs.sourcing_config_id is required")

    client_id = defaults.get("client_id", "")

    icp = defaults.get("icp_config") or {}
    market = icp.get("market") or {}
    segment = icp.get("segment") or {}
    persona = icp.get("persona") or {}

    job_titles_raw = persona.get("job_titles") or []
    # Their sample data shows titles sometimes packed into a single
    # comma-separated string inside a one-element list. Unpack that.
    job_titles: list = []
    for item in _as_list(job_titles_raw):
        if isinstance(item, str) and "," in item and len(_as_list(job_titles_raw)) == 1:
            job_titles.extend([t.strip() for t in item.split(",") if t.strip()])
        else:
            job_titles.append(item)

    return ParsedICP(
        sourcing_config_id=sourcing_config_id,
        client_id=client_id,
        market_name=market.get("name", ""),
        fixed_signals=_as_list(segment.get("fixed_signals")),
        custom_signals=_as_list(segment.get("custom_signals")),
        geos=_as_list(segment.get("geos")),
        company_sizes=_as_list(segment.get("company_sizes")),
        free_text_qualifier=segment.get("free_text_qualifier", "") or "",
        job_titles=job_titles,
    )


def build_search_queries(icp: ParsedICP, module_inputs: dict | None = None) -> list:
    """
    Turns the ICP signals into Google Jobs search queries.

    module_inputs.search_queries overrides everything when provided, which
    is the escape hatch for a human who knows exactly what to search.
    Otherwise queries are derived from fixed + custom signals, one query
    per signal per geo, so 'hiring for Anesthesia and CRNA' in
    'United States' becomes 'Anesthesia CRNA jobs United States'.
    """
    module_inputs = module_inputs or {}
    if module_inputs.get("search_queries"):
        return list(module_inputs["search_queries"])

    signals = icp.fixed_signals + icp.custom_signals
    geos = icp.geos or [""]

    queries = []
    for signal in signals:
        # Strip common lead-ins so the query reads like a jobs search.
        cleaned = (
            str(signal)
            .replace("hiring for", "")
            .replace("hiring", "")
            .strip()
        )
        if not cleaned:
            continue
        for geo in geos:
            q = f"{cleaned} jobs {geo}".strip()
            queries.append(q)

    return queries
