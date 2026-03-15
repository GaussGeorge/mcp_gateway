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
        description="纯测试桩：精准控制CPU燃烧时间(ms)和内存占用(MB)，用于生成无硬件抖动的基准对照数据。",
        category="benchmark",
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

    # ---- Phase 2: CPU Burn (tight float loop) ----
    cpu_start = time.time()
    burn_deadline = cpu_start + (cpu_burn_ms / 1000.0)
    iterations = 0
    x = 1.0

    while time.time() < burn_deadline:
        # Intensive floating-point computation to saturate a CPU core
        for _ in range(1000):
            x = (x * 1.000001 + 0.000001) / 1.000001
            iterations += 1

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
