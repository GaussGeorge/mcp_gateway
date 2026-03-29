// Package plangate 实现 PlanGate 方法 — Plan-Aware Gateway for MCP Tool Governance
//
// PlanGate 针对 MCP 多步工具调用场景下的级联算力浪费与服务过载问题，
// 提出三大核心创新机制:
//
// 创新点 1: Pre-flight Atomic Admission (DAG 预检准入)
//   - 客户端通过 X-Plan-DAG Header 提交完整 DAG 执行计划
//   - 网关在第 0 步原子性地计算全链路总价格并准入/拒绝
//   - 实现 "零级联算力浪费"
//
// 创新点 2: Budget Reservation (预算锁/远期价格锁定)
//   - 准入通过后为会话锁定当前价格快照
//   - 即使后续实时价格因拥塞暴涨，已准入会话仍按锁定价格结算
//   - 防止长链路 Agent 任务 "半路夭折"
//
// 创新点 3: Dual-Mode Governance (双模态异构治理)
//   - 有 X-Plan-DAG → Plan-and-Solve 模式 (创新点 1+2)
//   - 无 X-Plan-DAG → ReAct 模式 (标准 MCPGovernor 动态定价)
//
// HTTP 协议扩展:
//   - X-Plan-DAG:     完整 DAG JSON (首步携带)
//   - X-Session-ID:   会话 ID (后续步骤携带，用于查找预算锁)
//   - X-Total-Budget: 总预算 (首步携带，覆盖 DAG.Budget)
//
// 文件清单:
//   - doc.go               本文件，包级文档
//   - server.go            MCPDPServer 结构体、构造函数、工具注册
//   - session_manager.go   会话预算预留管理器 (HTTPBudgetReservationManager)
//   - dag_validation.go    DAG 类型定义与 Kahn 拓扑排序验证
//   - http_handlers.go     HTTP/JSON-RPC 请求分发 (ServeHTTP + MCP 方法处理)
//   - dual_mode_routing.go 双模态路由：P&S 预检准入 + ReAct 动态定价
package plangate
