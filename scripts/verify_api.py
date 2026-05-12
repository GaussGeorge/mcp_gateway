#!/usr/bin/env python3
"""Quick GLM API key verification."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'mcp_server'))

# Load .env
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line:
            k, _, v = line.partition('=')
            k, v = k.strip(), v.strip()
            if v and len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                v = v[1:-1]
            if k and v:
                os.environ[k] = v

key = os.environ.get('LLM_API_KEY', '')
base = os.environ.get('LLM_API_BASE', '')
model = os.environ.get('LLM_MODEL', '')
print(f"KEY: {key[:10]}...")
print(f"BASE: {base}")
print(f"MODEL: {model}")

import httpx
from openai import OpenAI
client = OpenAI(api_key=key, base_url=base, http_client=httpx.Client(proxy=None, timeout=30))
r = client.chat.completions.create(model=model, messages=[{"role": "user", "content": "Say OK"}], max_tokens=5)
print(f"RESPONSE: {r.choices[0].message.content}")
print("API_OK")
