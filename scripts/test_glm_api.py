"""Quick GLM-4-Flash connectivity test."""
import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'mcp_server'))

# Load .env
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
with open(env_path, encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')
            k, v = k.strip(), v.strip().strip('"')
            if k and v:
                os.environ.setdefault(k, v)

print(f"LLM_API_BASE: {os.environ.get('LLM_API_BASE', 'not set')}")
print(f"LLM_MODEL: {os.environ.get('LLM_MODEL', 'not set')}")
print(f"LLM_API_KEY: {os.environ.get('LLM_API_KEY', 'not set')[:20]}...")

import httpx
from openai import OpenAI

client = OpenAI(
    api_key=os.environ['LLM_API_KEY'],
    base_url=os.environ['LLM_API_BASE'],
    http_client=httpx.Client(proxy=None, timeout=30),
)

print('\n--- Test 1: GLM-4-Flash API ---')
start = time.time()
resp = client.chat.completions.create(
    model=os.environ['LLM_MODEL'],
    messages=[{'role': 'user', 'content': 'Say hello in one sentence.'}],
    max_tokens=50,
)
elapsed = (time.time() - start) * 1000
print(f"Response: {resp.choices[0].message.content}")
print(f"Latency: {elapsed:.0f}ms")
if resp.usage:
    print(f"Tokens: prompt={resp.usage.prompt_tokens}, completion={resp.usage.completion_tokens}")

# Test with_raw_response (for signal extraction)
print('\n--- Test 2: Raw Response Headers ---')
start = time.time()
raw_resp = client.chat.completions.with_raw_response.create(
    model=os.environ['LLM_MODEL'],
    messages=[{'role': 'user', 'content': 'Say OK.'}],
    max_tokens=10,
)
elapsed = (time.time() - start) * 1000
print(f"HTTP Status: {raw_resp.status_code}")
print(f"Latency: {elapsed:.0f}ms")
# Check for rate limit headers
for h in ['X-RateLimit-Remaining', 'RateLimit-Remaining', 'x-ratelimit-remaining',
          'X-RateLimit-Limit', 'x-ratelimit-limit', 'X-Request-Id']:
    val = raw_resp.headers.get(h)
    if val:
        print(f"  Header {h}: {val}")

parsed = raw_resp.parse()
print(f"Response: {parsed.choices[0].message.content}")

print('\n=== GLM-4-Flash API connectivity OK! ===')
