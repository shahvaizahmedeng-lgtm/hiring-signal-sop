"""Central configuration. Everything sensitive lives in environment variables."""

import os
from dotenv import load_dotenv

load_dotenv()

# Supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

# SerpAPI for Google Jobs results
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")

# OpenRouter for the ICP qualification LLM step
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-oss-20b:free")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Jina reader (free tier works without a key; key raises rate limits)
JINA_API_KEY = os.environ.get("JINA_API_KEY", "")

# Module behaviour
MAX_JOBS_PER_RUN = int(os.environ.get("MAX_JOBS_PER_RUN", "20"))
STATE_FILE = os.environ.get("STATE_FILE", "module_state.json")
# Default LLM qualification confidence floor; overridable per run via
# module_inputs.min_confidence.
DEFAULT_MIN_CONFIDENCE = int(os.environ.get("MIN_CONFIDENCE", "60"))
