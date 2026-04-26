# PlanGate 分布式实验方案

## 一、目标定位

- **论文目标**: 为了CCF-A 更稳的核心增量实验
- **核心问题**: PlanGate 的 session commitment 在多节点部署下是否保持 ABD 优势和零级联失败保证？
- **量化指标**: 2-3 节点 vs 单节点的 ABD 增幅 < 5%，吞吐线性度 > 0.8，额外延迟 < 2ms

---

## 二、架构方案：Redis + Session Affinity

```
            ┌── Nginx/HAProxy (X-Session-ID 一致性哈希) ──┐
            │                                                │
      Gateway-1            Gateway-2            Gateway-3
      (Go HTTP:9401)       (Go HTTP:9402)       (Go HTTP:9403)
            │                   │                     │
            └────── Redis (共享 session 状态) ─────────┘
                              │
                   Python MCP Backend Pool
                   (10 workers, 共享)
```

### 2.1 状态分层

| 层级 | 状态 | 存储位置 | 一致性要求 |
|------|------|----------|-----------|
| **强一致** | P&S 会话预留 (`reservations`) | Redis Hash | 每步必须查到 |
| **强一致** | ReAct 会话 (`sessions`) | Redis Hash | 步进必须全局有序 |
| **强一致** | 全局并发槽 (`sessionCap`) | Redis 计数器 + Lua | 原子 INCR/DECR |
| **强一致** | Agent 信誉表 (`reputationMgr`) | Redis Hash | 封禁全局生效 |
| **本地** | 动态定价 (`priceTableMap`) | 各节点独立计算 | 最终一致即可 |
| **本地** | 治理强度 (`intensityTracker`) | 各节点本地 EMA | 无需同步 |
| **本地** | 过载检测 (`overloadDetector`) | 各节点本地并发数 | 无需同步 |

### 2.2 Redis Key 设计

```
session:ps:{sessionID}     → Hash: LockedPrices, TotalCost, CurrentStep, ExpiresAt, NodeID
session:react:{sessionID}  → Hash: CurrentStep, CreatedAt, ExpiresAt, NodeID
global:session_count        → Counter (分布式信号量)
reputation:{agentID}        → Hash: Score, TotalSessions, Violations, IsBanned
```

### 2.3 Session Affinity 策略

- Nginx 按 `X-Session-ID` header 做一致性哈希: `hash $http_x_session_id consistent;`
- 同 session 所有步骤路由到同一节点（缓存命中率高）
- 节点故障时自动 fallover，从 Redis 恢复 session 状态
- 新 session step-0 轮询分配（无 session ID 时）

---

## 三、代码修改清单

### 3.1 新增 `SessionStore` 接口（~50 行）

```go
// plangate/session_store.go (新文件)
type SessionStore interface {
    // P&S
    ReservePS(ctx context.Context, id string, res *HTTPSessionReservation) error
    GetPS(ctx context.Context, id string) (*HTTPSessionReservation, error)
    AdvancePS(ctx context.Context, id string) (int, error)
    ReleasePS(ctx context.Context, id string) error
    
    // ReAct
    CreateReAct(ctx context.Context, id string, state *ReactSessionState) error
    GetReAct(ctx context.Context, id string) (*ReactSessionState, error)
    AdvanceReAct(ctx context.Context, id string) (int, error)
    DeleteReAct(ctx context.Context, id string) error
    ActiveCount(ctx context.Context) (int64, error)
    
    // 全局信号量
    AcquireSlot(ctx context.Context) (bool, error)
    ReleaseSlot(ctx context.Context) error
    
    // 信誉
    GetReputation(ctx context.Context, agentID string) (*ReputationScore, error)
    UpdateReputation(ctx context.Context, agentID string, score *ReputationScore) error
}
```

### 3.2 内存实现（兼容单节点）（~80 行）

```go
// plangate/session_store_memory.go (新文件)
// 包装现有 sync.Map 逻辑，保持单节点行为不变
type MemorySessionStore struct { ... }
```

### 3.3 Redis 实现（~150 行）

```go
// plangate/session_store_redis.go (新文件)
// go-redis/v9 + Lua 脚本原子操作
type RedisSessionStore struct {
    client *redis.Client
    prefix string
    maxSessions int
}
```

### 3.4 修改现有代码（~100 行改动）

| 文件 | 改动 | 行数 |
|------|------|------|
| `plangate/server.go` | 注入 `SessionStore` 接口替代 sync.Map | ~30 行 |
| `plangate/session_manager.go` | 委托给 `SessionStore` | ~40 行 |
| `plangate/reputation.go` | 委托给 `SessionStore` | ~20 行 |
| `cmd/gateway/main.go` | 新增 `--redis-addr` 参数，选择 backend | ~10 行 |

**总计: ~380-400 行新增/修改代码**

---

## 四、实验设计

### 4.1 实验矩阵

| 实验编号 | 节点数 | 后端 workers | 并发会话 | 负载 | N |
|----------|--------|-------------|----------|------|---|
| D1 | 1 (baseline) | 10 | 200 | 稳态 | 5 |
| D2 | 2 | 10 (共享) | 200 | 稳态 | 5 |
| D3 | 3 | 10 (共享) | 200 | 稳态 | 5 |
| D4 | 3 | 10 | 200 | 突发 (C=20, B=30) | 5 |
| D5 | 3 | 10 | 400 | 稳态 | 3 |
| D6 | 1→3 (动态扩容) | 10 | 200 | 稳态 | 3 |

### 4.2 度量指标

| 指标 | 定义 | 预期 |
|------|------|------|
| **ABD 增幅** | ABD(multi) - ABD(single) | < 5% |
| **吞吐线性度** | GP/s(N nodes) / (N × GP/s(1 node)) | > 0.8 |
| **Session 操作延迟** | Redis RTT 对 step 延迟的叠加 | < 2ms P99 |
| **Session Affinity 命中率** | 本地缓存命中 / 总请求 | > 90% |
| **故障恢复时间** | 节点宕机到会话重路由完成 | < 5s |
| **级联失败** | cascade_steps | 保持 0 (P&S), ~144 (ReAct bursty) |

### 4.3 对照组

- **NG-distributed**: 3 节点无治理（验证基础设施不引入干扰）
- **PG-single**: 1 节点 PlanGate（当前 baseline）
- **PG-distributed**: 3 节点 PlanGate + Redis
- **PG-no-affinity**: 3 节点 PlanGate + Redis，关闭 session affinity（worst case）

---

## 五、基础设施

### 5.1 本地开发环境（Docker Compose）

```yaml
version: "3.8"
services:
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
  
  gateway-1:
    build: .
    command: >
      ./gateway -mode mcpdp-real -port 9401
      -redis-addr redis:6379 -node-id gw1
    depends_on: [redis]
  
  gateway-2:
    build: .
    command: >
      ./gateway -mode mcpdp-real -port 9402
      -redis-addr redis:6379 -node-id gw2
    depends_on: [redis]
  
  gateway-3:
    build: .
    command: >
      ./gateway -mode mcpdp-real -port 9403
      -redis-addr redis:6379 -node-id gw3
    depends_on: [redis]
  
  nginx:
    image: nginx:alpine
    ports: ["9400:9400"]
    volumes: ["./nginx.conf:/etc/nginx/nginx.conf:ro"]
    depends_on: [gateway-1, gateway-2, gateway-3]
  
  backend:
    build: ./mcp_server
    command: python server.py --workers 10
    ports: ["5020:5020"]
```

### 5.2 CloudLab 部署（正式实验）

- **3 台 c220g5 节点**: 2×10-core Xeon, 192GB RAM, 25Gbps
- **节点分配**:
  - Node 1: Gateway-1 + Redis Master + Backend (5 workers)
  - Node 2: Gateway-2 + Redis Replica + Backend (5 workers)
  - Node 3: Gateway-3 + Redis Replica + Nginx LB + Agent Driver
- **网络**: 同 rack 内 25Gbps，RTT < 0.1ms

### 5.3 本地 qwen3.5-4b 可用于开发测试

- 端口 9999 已部署 qwen3.5-4b
- 用于快速迭代验证: 2-3 节点本地 Docker + 本地 LLM
- 正式实验仍建议使用 GLM-4-Flash (结果可比性)

---

## 六、执行时间表

| 阶段 | 内容 | 预估 |
|------|------|------|
| **P1: 接口抽象** | SessionStore 接口 + Memory 实现 + 单元测试 | 2-3 天 |
| **P2: Redis 实现** | Redis Store + Lua 脚本 + 集成测试 | 3-4 天 |
| **P3: LB 配置** | Nginx session affinity + Docker Compose | 1 天 |
| **P4: 本地验证** | Docker 3 节点 + 本地 qwen3.5-4b 跑通 | 1-2 天 |
| **P5: 微基准** | Redis RTT 开销测量 + affinity 命中率 | 1 天 |
| **P6: CloudLab 正式实验** | D1-D6 全部实验 + 数据收集 | 2-3 天 |
| **P7: 分析与论文** | 统计分析 + 论文新 section | 2-3 天 |
| **总计** | | **12-17 天** |

---

## 七、论文增量

### 7.1 新增内容

- **§5.11 Distributed Deployment Validation** (新 subsection)
  - 2-3 节点 Redis + Session Affinity 实验
  - ABD 增幅 + 吞吐线性度 + Session 延迟开销表
  - 故障恢复场景

### 7.2 更新内容

- **§7 Discussion**: 移除 "single-instance" limitation caveat，替换为实验验证结论
- **Abstract**: 加入 "distributed deployment with < X% ABD overhead"
- **§4 Implementation**: 加入 Redis SessionStore 架构描述

### 7.3 预期论文贡献

> 分布式验证证明 session commitment 在跨节点部署下保持有效:
> ABD 增幅 < 5%，吞吐近线性扩展 (0.8+ 线性度)，
> Redis 状态同步仅增加 < 2ms P99 延迟。

---

## 八、风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| Redis 延迟影响 step-0 决策 | 中 | ABD 增加 | Lua 脚本原子操作 + pipeline |
| Session Affinity 失效率高 | 低 | 额外 Redis 读 | 一致性哈希 + 备用路由 |
| CloudLab 节点申请缓慢 | 中 | 时间延迟 | 先在本地 Docker 完成所有开发 |
| 分布式定价不一致 | 低 | 轻微 ABD 波动 | 各节点独立定价已被设计为容忍 |
