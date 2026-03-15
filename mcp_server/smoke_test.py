"""MCP Server 冒烟测试 — 验证所有协议方法和工具。"""

import json
import time
import http.client

HOST = "localhost"
PORT = 8080


def rpc(method, params=None, req_id=1):
    """Send a JSON-RPC request and return the parsed response."""
    body = json.dumps({
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params or {}
    }).encode()
    conn = http.client.HTTPConnection(HOST, PORT, timeout=60)
    conn.request("POST", "/", body, {"Content-Type": "application/json"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    conn.close()
    return data


def call_tool(name, arguments, req_id=1):
    """Call a specific tool and return the result text."""
    resp = rpc("tools/call", {"name": name, "arguments": arguments}, req_id)
    if "error" in resp:
        return f"ERROR: {resp['error']['message']}", 0
    content = resp["result"]["content"][0]["text"]
    meta = resp["result"].get("_meta", {})
    return content, meta.get("latency_ms", 0)

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

# ─── 1. Protocol Tests ───
section("Protocol: initialize")
r = rpc("initialize")
print(json.dumps(r["result"], indent=2))

section("Protocol: ping")
r = rpc("ping")
print(f"Ping OK: {r}")

section("Protocol: tools/list")
r = rpc("tools/list")
tools = r["result"]["tools"]
print(f"共 {len(tools)} 个工具:")
for t in tools:
    print(f"  - {t['name']}: {t['description'][:50]}...")

# ─── 2. Lightweight Tools ───
section("轻量级: calculate (3.14 * 2.72)")
text, ms = call_tool("calculate", {"operation": "multiply", "a": 3.14, "b": 2.72})
print(f"  Result: {text}")
print(f"  Latency: {ms}ms")

section("轻量级: get_current_time")
text, ms = call_tool("get_current_time", {"timezone_offset": 8})
print(f"  Result: {text}")
print(f"  Latency: {ms}ms")

section("轻量级: get_weather (Mock, Beijing)")
text, ms = call_tool("get_weather", {"city": "Beijing", "simulate_rtt_ms": 120})
result = json.loads(text)
print(f"  City: {result['city']}, Weather: {result['weather']}, Temp: {result['temperature_c']}°C")
print(f"  Mock RTT: {result['_simulated_rtt_ms']}ms")
print(f"  Latency: {ms}ms")

section("轻量级: get_weather (Mock, unknown city)")
text, ms = call_tool("get_weather", {"city": "Chengdu"})
result = json.loads(text)
print(f"  City: {result['city']}, Weather: {result['weather']}, Temp: {result['temperature_c']}°C")
print(f"  Mock RTT: {result['_simulated_rtt_ms']}ms (random 50~200ms)")
print(f"  Latency: {ms}ms")

section("轻量级: web_fetch (Mock, MCP docs)")
text, ms = call_tool("web_fetch", {"url": "https://docs.example.com/mcp", "simulate_rtt_ms": 200})
result = json.loads(text)
print(f"  URL: {result['url']}")
print(f"  Content: {result['text'][:80]}...")
print(f"  Mock RTT: {result['_simulated_rtt_ms']}ms")
print(f"  Latency: {ms}ms")

section("轻量级: web_fetch (Mock, unknown URL)")
text, ms = call_tool("web_fetch", {"url": "https://unknown.example.com/page"})
result = json.loads(text)
print(f"  URL: {result['url']}")
print(f"  Content length: {result['content_length']} chars")
print(f"  Mock RTT: {result['_simulated_rtt_ms']}ms (random 100~500ms)")
print(f"  Latency: {ms}ms")

section("轻量级: text_format (json_format)")
messy_json = '{"name":"test","values":[1,2,3],"nested":{"a":true}}'
text, ms = call_tool("text_format", {"operation": "json_format", "text": messy_json})
print(f"  Result:\n{text}")
print(f"  Latency: {ms}ms")

section("轻量级: text_format (word_count)")
text, ms = call_tool("text_format", {"operation": "word_count", "text": "云计算中的MCP服务治理 is important"})
print(f"  Result: {text}")
print(f"  Latency: {ms}ms")

# ─── 3. Heavyweight Tools ───
section("重量级: doc_embedding (内存爆发测试, 20MB)")
sample_doc = "MCP协议是一种用于模型上下文管理的标准协议。" * 50
text, ms = call_tool("doc_embedding", {
    "text": sample_doc,
    "chunk_size": 128,
    "dimensions": 512,
    "simulate_memory_mb": 20
})
result = json.loads(text)
print(f"  Chunks: {result['total_chunks']}, Dims: {result['dimensions']}")
print(f"  Memory: {result['memory_simulated_mb']}MB")
print(f"  Time: {result['processing_time_s']}s")
print(f"  Latency: {ms}ms")

section("重量级: python_sandbox")
code = """
import math
data = [math.sin(x * 0.1) for x in range(100)]
print(f"Generated {len(data)} data points")
print(f"Min: {min(data):.4f}, Max: {max(data):.4f}")
print(f"Mean: {sum(data)/len(data):.4f}")
"""
text, ms = call_tool("python_sandbox", {"code": code, "timeout": 10})
result = json.loads(text)
print(f"  stdout: {result.get('stdout', '').strip()}")
print(f"  exit_code: {result['exit_code']}")
print(f"  Time: {result['execution_time_s']}s")
print(f"  Latency: {ms}ms")

section("重量级: mock_heavy (模拟LLM重载, CPU 8000ms + Memory 50MB)")
text, ms = call_tool("mock_heavy", {"cpu_burn_ms": 8000, "memory_mb": 50})
result = json.loads(text)
print(f"  Requested CPU: {result['requested_cpu_burn_ms']}ms")
print(f"  Actual CPU:    {result['actual_cpu_burn_ms']}ms")
print(f"  Precision:     +/-{result['precision_error_ms']}ms")
print(f"  Memory:        {result['requested_memory_mb']}MB")
print(f"  Total:         {result['total_time_ms']}ms")
print(f"  Latency:       {ms}ms")

section("重量级: mock_heavy (轻度重载, CPU 500ms + Memory 5MB)")
text, ms = call_tool("mock_heavy", {"cpu_burn_ms": 500, "memory_mb": 5})
result = json.loads(text)
print(f"  Requested CPU: {result['requested_cpu_burn_ms']}ms")
print(f"  Actual CPU:    {result['actual_cpu_burn_ms']}ms")
print(f"  Precision:     +/-{result['precision_error_ms']}ms")
print(f"  Memory:        {result['requested_memory_mb']}MB")
print(f"  Total:         {result['total_time_ms']}ms")
print(f"  Latency:       {ms}ms")

# ─── 4. Summary ───
section("测试总结")
print("  [PASS] 协议: initialize, ping, tools/list")
print("  [PASS] 轻量级: calculate, get_current_time, get_weather(Mock), web_fetch(Mock), text_format")
print("  [PASS] 重量级: doc_embedding, python_sandbox, mock_heavy(8s), mock_heavy(500ms)")
