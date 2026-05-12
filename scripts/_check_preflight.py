"""Pre-flight check script: scan for old prompts, API keys, and verify new prompts."""
import os
import re
import glob

OLD_PROMPTS = [
    "动态定价在微服务中的应用",
    "Token Bucket 和 Leaky Bucket 限流算法",
    "计划感知的网关能有效避免级联算力浪费",
    "多步 Agent 任务中被拒绝导致前序计算全部浪费",
]

NEW_PROMPTS = [
    "数据库索引",
    "队列和栈",
    "错误恢复机制",
    "可靠的软件系统需要清晰的日志和监控",
]

KEY_PATTERN = re.compile(r'sk-[A-Za-z0-9]{20,}|[a-f0-9]{32}\.[A-Za-z0-9]{20,}')

files = glob.glob('scripts/*.py') + glob.glob('scripts/*.sh')

print("=" * 60)
print("1. 旧 prompt 检查")
old_found = []
for f in files:
    content = open(f, encoding='utf-8', errors='ignore').read()
    for p in OLD_PROMPTS:
        if p in content:
            old_found.append((f, p))
if old_found:
    print("WARNING: 仍发现旧 prompt:")
    for f, p in old_found:
        print(f"  {f}: {p}")
else:
    print("OK: 所有旧 prompt 已移除")

print()
print("2. 新 prompt 检查")
for p in NEW_PROMPTS:
    found_in = [f for f in files if p in open(f, encoding='utf-8', errors='ignore').read()]
    if found_in:
        print(f"  OK: '{p}' 存在于 {found_in[0]}")
    else:
        print(f"  MISSING: '{p}' 未找到")

print()
print("3. API key 安全检查")
key_found = []
for f in files:
    content = open(f, encoding='utf-8', errors='ignore').read()
    for m in KEY_PATTERN.finditer(content):
        line_no = content[:m.start()].count('\n') + 1
        key_found.append((f, line_no))
if key_found:
    print("WARNING: 发现疑似明文 key:")
    for f, ln in key_found:
        print(f"  {f}:{ln}")
else:
    print("OK: 脚本中未发现明文 key")

if os.path.exists('.env'):
    lines = [l.strip() for l in open('.env', encoding='utf-8', errors='ignore') if re.search(r'KEY|key', l) and '=' in l]
    print(f".env 中检测到 key 变量: {len(lines)} 项（值不打印）")
else:
    print(".env 不存在")

print()
print("4. react_agent_client.py prompt pool 长度")
content = open('scripts/react_agent_client.py', encoding='utf-8', errors='ignore').read()
# Count entries in TASK_PROMPTS
matches = re.findall(r'"[^"]{10,}"', content)
print(f"  react_agent_client.py 字符串条目数（含所有字符串）: 约 {len(matches)}")
# Count the TASK_PROMPTS list specifically
task_section = re.search(r'TASK_PROMPTS\s*=\s*\[(.*?)\]', content, re.DOTALL)
if task_section:
    task_items = re.findall(r'"[^"]+",', task_section.group(1))
    print(f"  TASK_PROMPTS 列表条目数: {len(task_items)}")

print()
print("=== Pre-flight check done ===")
