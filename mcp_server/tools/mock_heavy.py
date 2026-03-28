"""纯测试桩 (Mock Heavy Tool) - 基准对照工具

功能：接收 cpu_burn_ms 和 memory_mb 参数，精准控制资源消耗。
作用：排除真实大模型的硬件抖动干扰，用纯数学模拟产生极度平滑的
     P95/P99 延迟 CDF 曲线，呈现最完美的算法对比。
"""

import json
import time
from tools import ToolDefinition, ToolRegistry


def register(registry: ToolRegistry):
    registry.register(ToolDefinition(
        name="mock_heavy",
        description="可控重载工具：精准控制CPU燃烧时间(ms)和内存占用(MB)，替代真实LLM消除硬件抖动。",
        category="heavyweight",
        input_schema={
            "type": "object",
            "properties": {
                "cpu_burn_ms": {
                    "type": "integer",
                    "description": "CPU燃烧时间(毫秒)，精确控制计算耗时",
                    "default": 1000
                },
                "memory_mb": {
                    "type": "integer",
                    "description": "内存占用(MB)，精确控制内存压力",
                    "default": 10
                }
            }
        },
        output_schema={
            "type": "object",
            "properties": {
                "requested_cpu_burn_ms": {"type": "integer"},
                "actual_cpu_burn_ms": {"type": "number"},
                "requested_memory_mb": {"type": "integer"},
                "total_time_ms": {"type": "number"}
            }
        },
        handler=execute
    ))


def execute(arguments: dict) -> str:
    cpu_burn_ms = arguments.get("cpu_burn_ms", 1000)
    memory_mb = arguments.get("memory_mb", 10)

    # Safety caps
    cpu_burn_ms = max(0, min(cpu_burn_ms, 60000))   # 0 ~ 60s
    memory_mb = max(0, min(memory_mb, 1024))         # 0 ~ 1GB

    start_time = time.time()

    # ---- Phase 1: Memory Allocation ----
    memory_block = None
    mem_alloc_ms = 0
    if memory_mb > 0:
        mem_start = time.time()
        memory_block = bytearray(memory_mb * 1024 * 1024)
        # Touch every page to force physical allocation
        for i in range(0, len(memory_block), 4096):
            memory_block[i] = 0xAB
        mem_alloc_ms = (time.time() - mem_start) * 1000

    # ---- Phase 2: Simulated Heavy Work (sleep-based) ----
    # 使用 time.sleep() 代替 CPU burn，原因：
    # 1. Python GIL 导致 CPU-bound 多线程无法并行，server 吞吐量极低
    # 2. 真实 MCP 场景中重量级工具（LLM 推理、外部 API）是 I/O bound
    # 3. sleep 期间释放 GIL，轻量级请求不被阻塞
    cpu_start = time.time()
    iterations = 0
    if cpu_burn_ms > 0:
        time.sleep(cpu_burn_ms / 1000.0)
        iterations = int(cpu_burn_ms * 1000)  # 模拟迭代次数

    actual_cpu_ms = (time.time() - cpu_start) * 1000

    # ---- Phase 3: Release Memory ----
    if memory_block is not None:
        del memory_block

    total_time_ms = (time.time() - start_time) * 1000

    return json.dumps({
        "requested_cpu_burn_ms": cpu_burn_ms,
        "actual_cpu_burn_ms": round(actual_cpu_ms, 2),
        "requested_memory_mb": memory_mb,
        "memory_alloc_ms": round(mem_alloc_ms, 2),
        "cpu_iterations": iterations,
        "total_time_ms": round(total_time_ms, 2),
        "precision_error_ms": round(abs(actual_cpu_ms - cpu_burn_ms), 2),
    }, ensure_ascii=False)
