import urllib.request, json, sys
try:
    r = urllib.request.urlopen('http://127.0.0.1:9999/v1/models', timeout=5)
    data = json.loads(r.read())
    print('vLLM: REACHABLE')
    for m in data.get('data', []):
        print(f"  model: {m['id']}")
except Exception as e:
    print(f'vLLM: NOT REACHABLE ({e})')
    sys.exit(1)
