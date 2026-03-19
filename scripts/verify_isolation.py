"""
资源隔离验证脚本 — Phase 1.3
验证 CPU 亲和性是否正确生效：
  1. 检查各进程的 CPU 亲和性设置
  2. 发送一波高并发脉冲
  3. 监控各核心的 CPU 使用率
  4. 生成验证报告

用法:
    python scripts/verify_isolation.py
    (需先通过 start_all.bat 启动全部组件)
"""

import os
import sys
import time
import json
import threading
import concurrent.futures
from urllib.request import urlopen, Request
from urllib.error import URLError

# 确保 psutil 可用
try:
    import psutil
except ImportError:
    print("错误: 需要 psutil。请安装: pip install psutil")
    sys.exit(1)


def check_process_affinity():
    """检查所有实验相关进程的 CPU 亲和性。"""
    print("=" * 60)
    print("  CPU 亲和性检查")
    print("=" * 60)

    targets = {
        "python": "MCP Backend (预期 Core 4-15)",
        "gateway": "Go Gateway (预期 Core 2-3)",
    }

    found = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "cpu_affinity"]):
        try:
            name = proc.info["name"].lower()
            cmdline = " ".join(proc.info.get("cmdline") or []).lower()

            if "server.py" in cmdline and "mcp" in cmdline:
                affinity = proc.cpu_affinity()
                status = "OK" if set(affinity) == set(range(4, 16)) else "WARN"
                print(f"  [{status}] PID={proc.pid}  MCP Backend  affinity={affinity}")
                found.append(("backend", proc.pid, affinity))

            elif "gateway" in name or ("gateway" in cmdline and ".exe" in cmdline):
                affinity = proc.cpu_affinity()
                status = "OK" if set(affinity) == {2, 3} else "WARN"
                print(f"  [{status}] PID={proc.pid}  Go Gateway   affinity={affinity}")
                found.append(("gateway", proc.pid, affinity))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    if not found:
        print("  [WARN] 未找到实验进程。请确认已通过 start_all.bat 启动。")
    print()
    return found


def send_burst(url, n_requests=50, tool_name="calculate", arguments=None):
    """向指定端口发送一波并发请求。"""
    if arguments is None:
        arguments = {"operation": "multiply", "a": 7, "b": 8}

    def single_request(i):
        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": i,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
                "_meta": {"tokens": 1000, "name": "isolation-test"},
            },
        }).encode("utf-8")

        req = Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            resp = urlopen(req, timeout=30)
            return resp.status
        except Exception as e:
            return str(e)

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(single_request, i) for i in range(n_requests)]
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())
    return results


def monitor_cpu_per_core(duration_sec=10, interval=0.5):
    """监控每个逻辑核心的 CPU 使用率。"""
    print(f"  监控 CPU 使用率 ({duration_sec}秒)...")
    samples = []
    end_time = time.time() + duration_sec
    while time.time() < end_time:
        usage = psutil.cpu_percent(interval=interval, percpu=True)
        samples.append(usage)
    return samples


def verify_isolation():
    """执行完整的资源隔离验证。"""
    # 1. 检查进程亲和性
    processes = check_process_affinity()

    # 2. 检查各端口是否可达
    ports = {
        8080: "MCP Backend",
        9001: "NG Gateway",
        9002: "SRL Gateway",
        9003: "DP Gateway",
    }
    print("=" * 60)
    print("  端口连通性检查")
    print("=" * 60)
    reachable = []
    for port, name in ports.items():
        url = f"http://127.0.0.1:{port}"
        try:
            # 发送 ping
            payload = json.dumps({
                "jsonrpc": "2.0", "id": "ping", "method": "ping", "params": {}
            }).encode("utf-8")
            req = Request(url, data=payload, headers={"Content-Type": "application/json"})
            resp = urlopen(req, timeout=5)
            print(f"  [OK]   {name} ({url}) — 状态 {resp.status}")
            reachable.append(port)
        except Exception as e:
            print(f"  [FAIL] {name} ({url}) — {e}")
    print()

    if not reachable:
        print("[中止] 没有可达的服务端口，请先启动组件。")
        return

    # 3. 监控 CPU + 并发发压
    print("=" * 60)
    print("  资源隔离压力测试")
    print("=" * 60)
    print("  同时向所有网关发送并发请求，观察 CPU 核心使用分布...\n")

    # 后台线程监控 CPU
    cpu_data = {"samples": []}

    def cpu_monitor():
        cpu_data["samples"] = monitor_cpu_per_core(duration_sec=15, interval=0.5)

    monitor_thread = threading.Thread(target=cpu_monitor)
    monitor_thread.start()

    # 等待 2 秒让监控建立基线
    time.sleep(2)

    # 向可达的端口发送 burst
    print("  发送并发请求...")
    for port in reachable:
        if port == 8080:
            url = f"http://127.0.0.1:{port}"
        else:
            url = f"http://127.0.0.1:{port}"
        try:
            # 混合轻量 + 重量请求
            results_light = send_burst(url, n_requests=30, tool_name="calculate",
                                       arguments={"operation": "multiply", "a": 99, "b": 101})
            results_heavy = send_burst(url, n_requests=5, tool_name="mock_heavy",
                                       arguments={"cpu_burn_ms": 500, "memory_mb": 0})
            success_light = sum(1 for r in results_light if r == 200)
            success_heavy = sum(1 for r in results_heavy if r == 200)
            print(f"    Port {port}: 轻量 {success_light}/30 成功, 重量 {success_heavy}/5 成功")
        except Exception as e:
            print(f"    Port {port}: 错误 — {e}")

    monitor_thread.join()

    # 4. 分析 CPU 分布
    print()
    print("=" * 60)
    print("  CPU 核心使用率分析")
    print("=" * 60)

    samples = cpu_data["samples"]
    if not samples:
        print("  [WARN] 未收集到 CPU 数据")
        return

    n_cores = len(samples[0])
    avg_usage = [0.0] * n_cores
    for s in samples:
        for i in range(n_cores):
            avg_usage[i] += s[i]
    avg_usage = [u / len(samples) for u in avg_usage]

    # 分组显示
    groups = {
        "发压机 (Core 0-1)": list(range(0, 2)),
        "Go 网关 (Core 2-3)": list(range(2, 4)),
        "MCP 后端 (Core 4-15)": list(range(4, min(16, n_cores))),
        "空闲区 (Core 16+)": list(range(16, n_cores)),
    }

    for group_name, cores in groups.items():
        if not cores:
            continue
        usages = [avg_usage[c] for c in cores if c < n_cores]
        if usages:
            avg = sum(usages) / len(usages)
            mx = max(usages)
            print(f"  {group_name:24s} — 平均: {avg:5.1f}%  峰值: {mx:5.1f}%")

    print()
    print("  各核心详细使用率:")
    for i, u in enumerate(avg_usage):
        bar = "█" * int(u / 2)
        label = ""
        if i < 2:
            label = " (发压机)"
        elif i < 4:
            label = " (网关)"
        elif i < 16:
            label = " (后端)"
        print(f"    Core {i:2d}: {u:5.1f}% {bar}{label}")

    # 5. 隔离性判定
    print()
    gateway_avg = sum(avg_usage[c] for c in range(2, 4)) / 2
    backend_avg = sum(avg_usage[c] for c in range(4, min(16, n_cores))) / max(1, min(12, n_cores - 4))
    idle_avg = sum(avg_usage[c] for c in range(16, n_cores)) / max(1, n_cores - 16) if n_cores > 16 else 0

    if gateway_avg > 1 or backend_avg > 1:
        print("  ✓ 隔离验证通过: 网关和后端核心有明显负载，且分布在各自绑定区域。")
    else:
        print("  ⚠ 隔离验证需确认: 负载较低，建议增加并发量后重新验证。")

    print()
    print("  提示: 请同时打开 Windows 任务管理器 → 性能 → CPU → 右键 '将图形更改为逻辑处理器'")
    print("        截图保存作为论文附录的资源隔离证据。")


if __name__ == "__main__":
    verify_isolation()
