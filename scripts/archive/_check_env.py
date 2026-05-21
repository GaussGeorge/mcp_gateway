"""Check environment variables for LLM API."""
import os

keys = ["AGENT_LLM_KEY", "LLM_API_KEY", "AGENT_LLM_MODEL", "AGENT_LLM_BASE_URL", "AGENT_LLM_BASE", "LLM_API_BASE"]
for k in keys:
    v = os.environ.get(k)
    if v:
        print(f"{k}: SET (length={len(v)})")
    else:
        print(f"{k}: MISSING")
