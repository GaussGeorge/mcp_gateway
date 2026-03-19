"""
monitor.py — MCP 服务治理实验 · 基础资源探针
========================================================
Phase 2.2 实现：
  - 每 500ms 采样一次 MCP 后端进程的 CPU% 和 Memory_MB
  - 落盘 CSV：timestamp, cpu_percent, memory_mb
  - 可按进程名 / PID / 端口匹配目标进程

用法:
  # 按端口匹配 (推荐)
  python scripts/monitor.py --port 8080 --output results/monitor_backend.csv

  # 按 PID 指定
  python scripts/monitor.py --pid 12345 --output results/monitor.csv

  # 同时监控多个端口
  python scripts/monitor.py --port 8080 --port 9003 --output results/monitor.csv

  # 持续运行直到 Ctrl+C
  python scripts/monitor.py --port 8080 --duration 0

  # 运行固定时长
  python scripts/monitor.py --port 8080 --duration 60 --output results/monitor.csv
"""

import argparse
import csv
import os
import signal
import sys
import time

try:
    import psutil
except ImportError:
    print("错误: 需要 psutil。请安装: pip install psutil")
    sys.exit(1)


# ══════════════════════════════════════════════════
# 进程查找
# ══════════════════════════════════════════════════
def find_process_by_port(port: int):
    """通过监听端口查找进程。"""
    for conn in psutil.net_connections(kind="tcp"):
        if conn.laddr.port == port and conn.status == "LISTEN":
            try:
                return psutil.Process(conn.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    return None


def find_processes(pids=None, ports=None):
    """查找所有目标进程，返回 {label: psutil.Process} 字典。"""
    targets = {}

    if pids:
        for pid in pids:
            try:
                p = psutil.Process(pid)
                targets[f"pid-{pid}"] = p
            except psutil.NoSuchProcess:
                print(f"[监控] 警告: PID {pid} 不存在")

    if ports:
        for port in ports:
            p = find_process_by_port(port)
            if p:
                targets[f"port-{port}"] = p
            else:
                print(f"[监控] 警告: 端口 {port} 未找到监听进程")

    return targets


# ══════════════════════════════════════════════════
# 监控循环
# ══════════════════════════════════════════════════
class Monitor:
    def __init__(self, targets: dict, interval_ms: int = 500, output: str = None, duration: float = 0):
        self.targets = targets
        self.interval = interval_ms / 1000.0
        self.output = output
        self.duration = duration  # 0 = 无限
        self.running = True
        self.records = []

        # 注册信号处理
        signal.signal(signal.SIGINT, self._handle_stop)
        signal.signal(signal.SIGTERM, self._handle_stop)

    def _handle_stop(self, signum, frame):
        print("\n[监控] 收到停止信号，正在保存...")
        self.running = False

    def run(self):
        """主监控循环。"""
        if not self.targets:
            print("[监控] 无目标进程，退出。")
            return

        # 初始化 CPU percent 计数器（首次调用返回 0）
        for label, proc in self.targets.items():
            try:
                proc.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # 打开 CSV 写入器（增量写入）
        csv_file = None
        csv_writer = None
        if self.output:
            os.makedirs(os.path.dirname(self.output) or ".", exist_ok=True)
            csv_file = open(self.output, "w", newline="", encoding="utf-8")
            csv_writer = csv.writer(csv_file)
            csv_writer.writerow(["timestamp", "label", "cpu_percent", "memory_mb"])

        print(f"[监控] 开始监控 {len(self.targets)} 个进程, 采样间隔 {self.interval*1000:.0f}ms")
        for label, proc in self.targets.items():
            try:
                print(f"  {label}: PID={proc.pid} ({proc.name()})")
            except psutil.NoSuchProcess:
                print(f"  {label}: 进程已消失")

        start_time = time.time()
        sample_count = 0

        try:
            while self.running:
                elapsed = time.time() - start_time
                if self.duration > 0 and elapsed >= self.duration:
                    break

                for label, proc in list(self.targets.items()):
                    try:
                        cpu = proc.cpu_percent(interval=None)
                        mem = proc.memory_info().rss / (1024 * 1024)  # bytes → MB
                        ts = time.time()

                        record = (f"{ts:.6f}", label, f"{cpu:.1f}", f"{mem:.1f}")
                        self.records.append(record)

                        if csv_writer:
                            csv_writer.writerow(record)

                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        print(f"[监控] {label}: 进程已消失，停止监控此目标")
                        del self.targets[label]
                        if not self.targets:
                            self.running = False
                        break

                sample_count += 1
                if sample_count % 20 == 0:  # 每 10 秒打印一次摘要
                    self._print_live(elapsed)

                time.sleep(self.interval)

        finally:
            if csv_file:
                csv_file.flush()
                csv_file.close()

        # 最终摘要
        elapsed = time.time() - start_time
        self._print_summary(elapsed, sample_count)

    def _print_live(self, elapsed):
        """实时状态打印。"""
        parts = []
        for label, proc in self.targets.items():
            try:
                cpu = proc.cpu_percent(interval=None)
                mem = proc.memory_info().rss / (1024 * 1024)
                parts.append(f"{label}: CPU={cpu:.0f}% MEM={mem:.0f}MB")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                parts.append(f"{label}: N/A")
        print(f"  [{elapsed:.0f}s] {' | '.join(parts)}")

    def _print_summary(self, elapsed, sample_count):
        """运行结束摘要。"""
        print(f"\n{'=' * 55}")
        print(f"  资源监控摘要")
        print(f"{'=' * 55}")
        print(f"  监控时长:  {elapsed:.1f}s")
        print(f"  采样点数:  {sample_count}")

        if self.records:
            # 按 label 分组统计
            from collections import defaultdict
            stats = defaultdict(lambda: {"cpu": [], "mem": []})
            for ts, label, cpu, mem in self.records:
                stats[label]["cpu"].append(float(cpu))
                stats[label]["mem"].append(float(mem))

            for label, data in sorted(stats.items()):
                cpus = data["cpu"]
                mems = data["mem"]
                print(f"\n  [{label}]")
                print(f"    CPU%%:  avg={sum(cpus)/len(cpus):.1f}%  max={max(cpus):.1f}%  min={min(cpus):.1f}%")
                print(f"    MEM:   avg={sum(mems)/len(mems):.1f}MB  max={max(mems):.1f}MB")

        if self.output:
            print(f"\n  数据已保存: {self.output} ({len(self.records)} 条记录)")
        print(f"{'=' * 55}")


# ══════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="MCP 实验资源探针 — 进程级 CPU/内存监控",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--pid", type=int, action="append", default=None,
                        help="目标进程 PID (可多次指定)")
    parser.add_argument("--port", type=int, action="append", default=None,
                        help="目标监听端口 (可多次指定)")
    parser.add_argument("--interval", type=int, default=500,
                        help="采样间隔/ms (default: 500)")
    parser.add_argument("--duration", type=float, default=0,
                        help="监控时长/秒, 0=无限 (default: 0)")
    parser.add_argument("--output", "-o", type=str, default=None,
                        help="输出 CSV 路径")
    args = parser.parse_args()

    if not args.pid and not args.port:
        print("错误: 请指定 --pid 或 --port")
        parser.print_help()
        sys.exit(1)

    targets = find_processes(pids=args.pid, ports=args.port)
    if not targets:
        print("[监控] 未找到任何目标进程，退出。")
        sys.exit(1)

    monitor = Monitor(
        targets=targets,
        interval_ms=args.interval,
        output=args.output,
        duration=args.duration,
    )
    monitor.run()


if __name__ == "__main__":
    main()
