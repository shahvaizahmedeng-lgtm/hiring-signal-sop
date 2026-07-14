"""
state.py

Runtime state for the module, per the SOP: declarations live on the
sourcing_modules row, but actual state VALUES live on this server.

App state used by this module:
  backfill_done (boolean, default false): false on first run, the module
  fetches the full current set of postings; set true after that completes.
  Subsequent runs read it and fetch incrementally (fewer pages), which is
  what lets a sourcing config behave as an evergreen campaign.

Metadata recorded (write-only, debugging):
  last_search_queries: the exact queries sent to SerpAPI on the last run.

Stored as a local JSON file keyed by sourcing_config_id. In production
this would move to a small table or Redis, but the interface below stays
identical, which is the point of isolating it here.
"""

import json
import logging
import os
from threading import Lock

from config import STATE_FILE

logger = logging.getLogger(__name__)
_lock = Lock()


def _load() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("State file unreadable (%s); starting fresh", e)
        return {}


def _save(state: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)  # atomic on POSIX


def get_state(sourcing_config_id: str) -> dict:
    with _lock:
        return _load().get(sourcing_config_id, {"backfill_done": False})


def update_state(sourcing_config_id: str, **kwargs) -> dict:
    with _lock:
        state = _load()
        entry = state.get(sourcing_config_id, {"backfill_done": False})
        entry.update(kwargs)
        state[sourcing_config_id] = entry
        _save(state)
        return entry
