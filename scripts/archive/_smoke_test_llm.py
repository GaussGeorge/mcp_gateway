"""Minimal smoke test: load .env -> make 1 LLM API call -> confirm neutral prompt used."""
import os
import sys

# --- Load .env manually ---
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
if os.path.exists(env_path):
    for line in open(env_path, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k not in os.environ:  # don't override shell env
                os.environ[k] = v
    print("[smoke] .env loaded")

llm_base = os.getenv("AGENT_LLM_BASE_URL", os.getenv("AGENT_LLM_BASE", os.getenv("LLM_API_BASE", "")))
llm_key = os.getenv("AGENT_LLM_KEY", os.getenv("LLM_API_KEY", ""))
llm_model = os.getenv("AGENT_LLM_MODEL", os.getenv("LLM_MODEL", "glm-4-flash"))

print(f"[smoke] base: {llm_base}")
print(f"[smoke] key: {'SET (len=' + str(len(llm_key)) + ')' if llm_key else 'MISSING'}")
print(f"[smoke] model: {llm_model}")

if not llm_key:
    print("[smoke] ERROR: No API key found. Set LLM_API_KEY or AGENT_LLM_KEY.")
    sys.exit(1)

if not llm_base:
    print("[smoke] ERROR: No base URL found. Set LLM_API_BASE or AGENT_LLM_BASE_URL.")
    sys.exit(1)

# Check neutral prompt exists in react_agent_client
client_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "react_agent_client.py")
content = open(client_path, encoding="utf-8").read()
neutral_prompts = ["数据库索引", "队列和栈", "错误恢复机制"]
for p in neutral_prompts:
    if p not in content:
        print(f"[smoke] ERROR: neutral prompt '{p}' missing from react_agent_client.py")
        sys.exit(1)
print(f"[smoke] neutral prompts OK in react_agent_client.py")

old_prompts = ["动态定价在微服务中的应用", "Token Bucket 和 Leaky Bucket 限流算法"]
for p in old_prompts:
    if p in content:
        print(f"[smoke] ERROR: old prompt '{p}' still in react_agent_client.py")
        sys.exit(1)
print(f"[smoke] old prompts NOT in react_agent_client.py (good)")

# Make 1 real API call
try:
    import httpx
    from openai import OpenAI

    client = OpenAI(
        api_key=llm_key,
        base_url=llm_base,
        http_client=httpx.Client(proxy=None, timeout=30),
    )
    print(f"[smoke] Calling {llm_model} with neutral prompt...")
    resp = client.chat.completions.create(
        model=llm_model,
        messages=[{"role": "user", "content": "请用不超过20个字解释什么是数据库索引。"}],
        max_tokens=50,
        temperature=0.7,
    )
    answer = resp.choices[0].message.content
    print(f"[smoke] LLM response: {answer[:100]}")
    print(f"[smoke] tokens used: {resp.usage.total_tokens if resp.usage else 'N/A'}")
    print("[smoke] SUCCESS: API call succeeded with neutral prompt")
except Exception as e:
    print(f"[smoke] FAILED: {type(e).__name__}: {e}")
    sys.exit(1)
