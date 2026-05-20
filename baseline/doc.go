// doc.go
// Package baseline 提供 MCP 服务治理实验的两个基线对比方法。
//
// # 方法概览
//
// 本包实现了实验设计中定义的两个 baseline 网关，与主方法 Dynamic Pricing (DP)
// 进行对比实验：
//
//   - NGGateway (No Governance): 无治理基线，所有请求直接透传
//   - SRLGateway (Static Rate Limit): 静态限流基线，使用令牌桶 + 并发数限制
//
// # 实验公平性保证
//
// 为确保与 DP 方法的公平对比，所有 baseline 都遵循以下原则：
//
//  1. 协议一致性：使用相同的 JSON-RPC 2.0 + MCP 协议栈
//  2. 接口一致性：实现相同的 http.Handler 接口，支持 initialize/tools-list/tools-call/ping
//  3. 工具注册一致性：使用相同的 RegisterTool API 注册工具
//  4. 指标采集一致性：响应中统一携带 _meta（price, name），便于下游指标统一收集
//  5. 统计接口一致性：均提供 GetStats 方法获取运行时统计
//
// # 使用示例
//
// No Governance:
//
//	ng := baseline.NewNGGateway("ng-server")
//	ng.RegisterTool(tool, handler)
//	http.ListenAndServe(":8080", ng)
//
// Static Rate Limit:
//
//	srl := baseline.NewSRLGateway("srl-server", baseline.SRLConfig{
//	    QPS:            50,    // 每秒允许 50 个请求
//	    BurstSize:      100,   // 突发容量 100
//	    MaxConcurrency: 20,    // 最多 20 个并发
//	})
//	srl.RegisterTool(tool, handler)
//	http.ListenAndServe(":8080", srl)
//
// # SRL 参数调优指南
//
// 为确保实验公平，SRL 的 QPS 参数应根据 DP 在相同负载下的通过率来校准：
//
//  1. 先跑一次 DP 实验，记录平均通过 QPS（如 Poisson, heavy_ratio=0.3 下约 50 req/s）
//  2. 将 SRL 的 QPS 设为同一水平（QPS=50）
//  3. BurstSize 设为 QPS 的 2-3 倍
//  4. MaxConcurrency 设为 CPU 核数 × 2-4
//
// 这样保证 SRL 和 DP 在稳态下的总通过量接近，核心差异体现在：
//   - SRL 对轻量/重量请求"盲拒"（随机公平）
//   - DP 对低预算重量请求优先拒绝（经济公平）
package baseline
