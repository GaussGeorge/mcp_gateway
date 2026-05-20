"""测试脚本：验证无菌实验室模式和真实战场模式的全部工具"""
import json
import urllib.request
import sys


def call_tool(port, name, args):
    req = json.dumps({
        'jsonrpc': '2.0', 'id': 1,
        'method': 'tools/call',
        'params': {'name': name, 'arguments': args}
    }).encode()
    r = urllib.request.urlopen(urllib.request.Request(
        f'http://127.0.0.1:{port}/',
        data=req,
        headers={'Content-Type': 'application/json'}
    ), timeout=120)
    resp = json.loads(r.read())
    if 'error' in resp:
        return {'_rpc_error': resp['error']}, {}
    content = resp.get('result', {}).get('content', [{}])[0].get('text', '')
    meta = resp.get('result', {}).get('_meta', {})
    return json.loads(content), meta


def test_sterile(port):
    print("=" * 60)
    print("  无菌实验室模式 (Sterile Lab) — 3 Tools")
    print("=" * 60)
    passed = 0

    # 1. calculate
    print("\n[1/3] calculate (multiply 7×8)")
    result, meta = call_tool(port, 'calculate', {'operation': 'multiply', 'a': 7, 'b': 8})
    print(f"  Result: {result.get('result', result)}")
    print(f"  Latency: {meta.get('latency_ms')}ms")
    if result.get('result') == 56:
        print("  ✓ PASS")
        passed += 1
    else:
        print("  ✗ FAIL")

    # 2. web_fetch (mock)
    print("\n[2/3] web_fetch (mock)")
    result, meta = call_tool(port, 'web_fetch', {'url': 'https://example.com', 'max_length': 200})
    print(f"  Mock: {result.get('_mock')}")
    print(f"  Simulated RTT: {result.get('_simulated_rtt_ms')}ms")
    print(f"  Latency: {meta.get('latency_ms')}ms")
    if result.get('_mock') == True:
        print("  ✓ PASS")
        passed += 1
    else:
        print("  ✗ FAIL")

    # 3. mock_heavy
    print("\n[3/3] mock_heavy (cpu=500ms, mem=5MB)")
    result, meta = call_tool(port, 'mock_heavy', {'cpu_burn_ms': 500, 'memory_mb': 5})
    print(f"  Actual CPU: {result.get('actual_cpu_burn_ms')}ms")
    print(f"  Precision: {result.get('precision_error_ms')}ms")
    print(f"  Latency: {meta.get('latency_ms')}ms")
    if result.get('actual_cpu_burn_ms') and result.get('actual_cpu_burn_ms') > 400:
        print("  ✓ PASS")
        passed += 1
    else:
        print("  ✗ FAIL")

    print(f"\n{'✅' if passed==3 else '❌'} 无菌实验室模式: {passed}/3 通过")
    return passed == 3


def test_battlefield(port):
    print("\n" + "=" * 60)
    print("  真实战场模式 (Real Battlefield) — 7 Tools")
    print("=" * 60)
    passed = 0

    # 1. calculate
    print("\n[1/7] calculate (sqrt 144)")
    result, meta = call_tool(port, 'calculate', {'operation': 'sqrt', 'a': 144})
    print(f"  Result: {result.get('result', result)}")
    print(f"  Latency: {meta.get('latency_ms')}ms")
    if result.get('result') == 12.0:
        print("  ✓ PASS")
        passed += 1
    else:
        print("  ✗ FAIL")

    # 2. get_weather (mock)
    print("\n[2/7] get_weather (mock)")
    result, meta = call_tool(port, 'get_weather', {'city': 'Beijing'})
    print(f"  Mock: {result.get('_mock')}")
    print(f"  Temp: {result.get('temperature')}")
    print(f"  Latency: {meta.get('latency_ms')}ms")
    if result.get('_mock') == True:
        print("  ✓ PASS")
        passed += 1
    else:
        print("  ✗ FAIL")

    # 3. web_fetch (mock)
    print("\n[3/7] web_fetch (mock)")
    result, meta = call_tool(port, 'web_fetch', {'url': 'https://example.com', 'max_length': 100})
    print(f"  Mock: {result.get('_mock')}")
    print(f"  Latency: {meta.get('latency_ms')}ms")
    if result.get('_mock') == True:
        print("  ✓ PASS")
        passed += 1
    else:
        print("  ✗ FAIL")

    # 4. text_format
    print("\n[4/7] text_format (word_count)")
    result, meta = call_tool(port, 'text_format', {'operation': 'word_count', 'text': 'Hello world from MCP'})
    print(f"  Result: {result}")
    print(f"  Latency: {meta.get('latency_ms')}ms")
    if 'error' not in result:
        print("  ✓ PASS")
        passed += 1
    else:
        print(f"  ✗ FAIL: {result}")

    # 5. llm_reason (real LLM)
    print("\n[5/7] llm_reason (summarize — real GPU)")
    result, meta = call_tool(port, 'llm_reason', {
        'operation': 'summarize',
        'text': '人工智能正在改变世界各个领域。在医疗健康方面，AI辅助诊断已经达到了专家级水平。',
        'max_tokens': 50
    })
    if 'error' in result:
        print(f"  ✗ FAIL: {result.get('error')}")
    else:
        print(f"  Result: {result.get('result', '')[:80]}...")
        print(f"  TTFT: {result.get('metrics', {}).get('ttft_ms')}ms")
        print(f"  TPS: {result.get('metrics', {}).get('tokens_per_sec')}")
        print(f"  Latency: {meta.get('latency_ms')}ms")
        print("  ✓ PASS")
        passed += 1

    # 6. doc_embedding (real Embedding API)
    print("\n[6/7] doc_embedding (real GPU)")
    result, meta = call_tool(port, 'doc_embedding', {
        'text': '这是一段用于测试向量化的文本。MCP治理系统需要处理各种复杂的工具调用场景，包括轻量级和重量级工具的混合负载。',
        'chunk_size': 50
    })
    if 'error' in result:
        print(f"  ✗ FAIL: {result.get('error')}")
        print(f"  Hint: {result.get('hint', 'N/A')}")
    else:
        print(f"  Chunks: {result.get('total_chunks')}")
        print(f"  Dimensions: {result.get('dimensions')}")
        print(f"  Model: {result.get('embedding_model')}")
        print(f"  API time: {result.get('api_time_s')}s")
        print(f"  Latency: {meta.get('latency_ms')}ms")
        print("  ✓ PASS")
        passed += 1

    # 7. python_sandbox (real subprocess)
    print("\n[7/7] python_sandbox (real CPU)")
    result, meta = call_tool(port, 'python_sandbox', {
        'code': 'import math\nprint(f"Pi = {math.pi:.10f}")\nprint(f"Factorial(20) = {math.factorial(20)}")',
        'timeout': 10
    })
    if 'error' in result:
        print(f"  ✗ FAIL: {result.get('error')}")
    else:
        print(f"  Exit code: {result.get('exit_code')}")
        print(f"  Output: {result.get('stdout', '').strip()}")
        print(f"  Time: {result.get('execution_time_s')}s")
        print(f"  Latency: {meta.get('latency_ms')}ms")
        if result.get('exit_code') == 0:
            print("  ✓ PASS")
            passed += 1
        else:
            print("  ✗ FAIL")

    print(f"\n{'✅' if passed==7 else '⚠️'} 真实战场模式: {passed}/7 通过")
    return passed


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'sterile'
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8081

    if mode == 'sterile':
        test_sterile(port)
    elif mode == 'battlefield':
        test_battlefield(port)
    elif mode == 'all':
        test_sterile(int(sys.argv[2]) if len(sys.argv) > 2 else 8081)
        test_battlefield(int(sys.argv[3]) if len(sys.argv) > 3 else 8082)
