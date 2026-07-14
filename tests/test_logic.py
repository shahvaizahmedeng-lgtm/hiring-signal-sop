"""
test_logic.py

Tests the pure logic against THEIR exact sample input from the SOP:
parsing, query building, domain standardisation, LinkedIn tag extraction,
drop-gate behaviour, and country code mapping. No network needed.

Run:  python3 tests/test_logic.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from icp_parser import parse_default_inputs, build_search_queries, to_country_code
from domain_resolver import standardise_domain, extract_linkedin_tag
from normaliser import normalise, DropGate
from qualifier import passes_confidence
import jd_fetcher

PASS, FAIL = 0, 0


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


with open(os.path.join(os.path.dirname(__file__), "sample_input.json")) as f:
    SAMPLE = json.load(f)

print("\n== ICP parsing (their exact SOP sample) ==")
icp = parse_default_inputs(SAMPLE)
check("sourcing_config_id extracted", icp.sourcing_config_id == "2asifsouahfaiusf-fsafnaa-asfasf")
check("fixed_signals extracted", icp.fixed_signals == ["hiring for Anesthesia and CRNA"])
check("geos extracted", icp.geos == ["United States"])
check("5 size bands", len(icp.company_sizes) == 5)
check("free_text_qualifier present", "Anesthesia" in icp.free_text_qualifier)
check("comma-packed job_titles unpacked", len(icp.job_titles) == 17, f"got {len(icp.job_titles)}")

print("\n== Query building ==")
queries = build_search_queries(icp)
check("one query per signal x geo", queries == ["Anesthesia and CRNA jobs United States"], str(queries))
override = build_search_queries(icp, {"search_queries": ["custom query"]})
check("module_inputs override wins", override == ["custom query"])

print("\n== Missing required field fails loudly ==")
try:
    parse_default_inputs({"default_inputs": {}})
    check("missing sourcing_config_id raises", False)
except ValueError:
    check("missing sourcing_config_id raises", True)

print("\n== Domain standardisation (spec: domain only, no https) ==")
check("strips scheme+www+path", standardise_domain("https://www.Strategy-Group.ca/careers?x=1") == "strategy-group.ca")
check("bare domain passes", standardise_domain("mfg.com") == "mfg.com")
check("garbage returns None", standardise_domain("not a domain") is None)
check("empty returns None", standardise_domain("") is None)

print("\n== LinkedIn tag (spec: tag only, not URL) ==")
check("extracts tag", extract_linkedin_tag("https://www.linkedin.com/company/strategysearchgroup/") == "strategysearchgroup")
check("no url -> None", extract_linkedin_tag("") is None)
check("non-linkedin -> None", extract_linkedin_tag("https://example.com") is None)

print("\n== Country codes ==")
check("United States -> US", to_country_code("United States") == "US")
check("unknown -> None (nullable, never guessed)", to_country_code("Atlantis") is None)

print("\n== Normaliser + drop gate ==")
cand = {
    "job_id": "abc123", "title": "CRNA - Full Time", "company_name": "Mercy Health",
    "location": "Atlanta, United States",
    "description": "Mercy Health is hiring a full-time CRNA to join our anesthesia team...",
    "apply_url": "https://g.co/job1",
}
verdict = {"qualified": True, "confidence": 88, "reason": "healthcare provider hiring CRNA",
           "estimated_size_band": "1001-5000"}

row = normalise(cand, verdict, "mercy.com", "https://linkedin.com/company/mercy-health/", icp)
check("domain in row", row["standardised_domain"] == "mercy.com")
check("linkedin tag not url", row["company_linkedin_tag"] == "mercy-health")
check("geography inferred US", row["geography"] == "US")
check("job_id in custom_fields (dedupe key)", row["custom_fields"]["job_id"] == "abc123")
check("size from verdict", row["company_size"] == "1001-5000")
check("config id attached", row["sourcing_config_id"] == icp.sourcing_config_id)
check("full job_description stored", "anesthesia team" in row["custom_fields"]["job_description"])
check("job_url from apply_url", row["custom_fields"]["job_url"] == "https://g.co/job1")

try:
    normalise(cand, verdict, None, None, icp)
    check("no domain -> DropGate", False)
except DropGate:
    check("no domain -> DropGate", True)

try:
    normalise({**cand, "company_name": ""}, verdict, "mercy.com", None, icp)
    check("no company_name -> DropGate", False)
except DropGate:
    check("no company_name -> DropGate", True)

# LinkedIn absent must NOT gate (nullable by spec)
row2 = normalise(cand, verdict, "mercy.com", None, icp)
check("missing linkedin does NOT gate", row2["company_linkedin_tag"] is None)

print("\n== Confidence gate (min_confidence) ==")
check("qualified + above threshold passes", passes_confidence({"qualified": True, "confidence": 80}, 60))
check("qualified + exactly at threshold passes", passes_confidence({"qualified": True, "confidence": 60}, 60))
check("qualified + below threshold held", not passes_confidence({"qualified": True, "confidence": 59}, 60))
check("unqualified never passes", not passes_confidence({"qualified": False, "confidence": 99}, 60))
check("missing confidence treated as 0", not passes_confidence({"qualified": True}, 60))

print("\n== JD fetcher fallback (best-effort, never fatal) ==")
check("no url -> returns fallback", jd_fetcher.fetch_full_description("", "fallback text") == "fallback text")


class _FakeResp:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"status {self.status_code}")


_orig_get = jd_fetcher.requests.get

# A rich live fetch replaces the thin SerpAPI fallback.
jd_fetcher.requests.get = lambda *a, **k: _FakeResp(200, "L" * 500)
check("rich live fetch wins over fallback",
      jd_fetcher.fetch_full_description("https://x.co/j", "short") == "L" * 500)

# A network failure falls back to the SerpAPI description, never raising.
import requests as _rq
def _boom(*a, **k):
    raise _rq.ConnectionError("down")
jd_fetcher.requests.get = _boom
check("fetch failure -> fallback (no raise)",
      jd_fetcher.fetch_full_description("https://x.co/j", "serp fallback") == "serp fallback")

# A thin live result does not clobber a longer fallback.
jd_fetcher.requests.get = lambda *a, **k: _FakeResp(200, "tiny")
long_fallback = "F" * 400
check("thin live result keeps longer fallback",
      jd_fetcher.fetch_full_description("https://x.co/j", long_fallback) == long_fallback)

jd_fetcher.requests.get = _orig_get

print(f"\n{'=' * 40}\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
