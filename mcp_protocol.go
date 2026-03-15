// mcp_protocol.go
// MCP (Model Context Protocol) 协议类型定义
// 基于 JSON-RPC 2.0 标准实现，适用于工具调用 (tools/call) 场景下的服务治理
package mcpgov

import (
	"encoding/json"
	"fmt"
)

// ==================== JSON-RPC 2.0 基础协议类型 ====================

// JSONRPCVersion JSON-RPC 协议版本号，固定为 "2.0"
const JSONRPCVersion = "2.0"

// JSON-RPC 2.0 标准错误码
const (
	CodeParseError     = -32700 // 解析错误：服务端收到了无效的 JSON
	CodeInvalidRequest = -32600 // 无效请求：发送的 JSON 不是一个有效的 JSON-RPC 请求
	CodeMethodNotFound = -32601 // 方法未找到：方法不存在或不可用
	CodeInvalidParams  = -32602 // 无效参数：方法参数无效
	CodeInternalError  = -32603 // 内部错误：JSON-RPC 内部错误

	// === MCP 服务治理自定义错误码 (保留范围: -32000 ~ -32099) ===
	CodeOverloaded        = -32001 // 服务过载：触发负载削减 (Load Shedding)
	CodeRateLimited       = -32002 // 客户端被限流 (Rate Limited)
	CodeTokenInsufficient = -32003 // 令牌不足以支付工具调用费用
)

// JSONRPCRequest JSON-RPC 2.0 请求对象
// MCP 协议中所有客户端→服务端的消息都遵循此格式
// 示例：
//
//	{
//	  "jsonrpc": "2.0",
//	  "id": 1,
//	  "method": "tools/call",
//	  "params": { "name": "get_weather", "arguments": {"city": "北京"}, "_meta": {"tokens": 100} }
//	}
type JSONRPCRequest struct {
	JSONRPC string          `json:"jsonrpc"`          // 必须为 "2.0"
	ID      interface{}     `json:"id,omitempty"`     // 请求标识符 (string|number|null)，通知消息不含 ID
	Method  string          `json:"method"`           // 调用的方法名，如 "tools/call", "tools/list"
	Params  json.RawMessage `json:"params,omitempty"` // 方法参数，延迟解析 (结构因方法而异)
}

// JSONRPCResponse JSON-RPC 2.0 响应对象
// 服务端→客户端的消息格式，result 和 error 互斥 (二选一)
type JSONRPCResponse struct {
	JSONRPC string      `json:"jsonrpc"`          // 必须为 "2.0"
	ID      interface{} `json:"id"`               // 与请求对应的标识符
	Result  interface{} `json:"result,omitempty"` // 调用成功时的返回值
	Error   *RPCError   `json:"error,omitempty"`  // 调用失败时的错误对象
}

// RPCError JSON-RPC 2.0 错误对象
// 当工具调用因治理策略被拒绝时，Data 字段会携带当前价格信息供客户端缓存
type RPCError struct {
	Code    int         `json:"code"`           // 错误码 (整数)
	Message string      `json:"message"`        // 错误简要描述
	Data    interface{} `json:"data,omitempty"` // 附加数据 (如当前价格)
}

// Error 实现 error 接口，使 RPCError 可作为 Go error 使用
func (e *RPCError) Error() string {
	return fmt.Sprintf("JSON-RPC error %d: %s", e.Code, e.Message)
}

// ==================== MCP 协议方法名常量 ====================

const (
	MethodInitialize = "initialize" // 初始化握手
	MethodToolsList  = "tools/list" // 列出可用工具
	MethodToolsCall  = "tools/call" // 调用工具
	MethodPing       = "ping"       // 健康检查
)

// ==================== MCP 初始化相关类型 ====================

// MCPInitializeParams initialize 请求参数
type MCPInitializeParams struct {
	ProtocolVersion string         `json:"protocolVersion"` // 客户端支持的协议版本
	ClientInfo      Implementation `json:"clientInfo"`      // 客户端信息
	Capabilities    interface{}    `json:"capabilities,omitempty"`
}

// MCPInitializeResult initialize 响应结果
type MCPInitializeResult struct {
	ProtocolVersion string             `json:"protocolVersion"` // 协商后的协议版本
	ServerInfo      Implementation     `json:"serverInfo"`      // 服务端信息
	Capabilities    ServerCapabilities `json:"capabilities"`    // 服务端能力声明
}

// Implementation 客户端/服务端实现信息
type Implementation struct {
	Name    string `json:"name"`
	Version string `json:"version"`
}

// ServerCapabilities 服务端能力声明
type ServerCapabilities struct {
	Tools *ToolsCapability `json:"tools,omitempty"` // 工具能力
}

// ToolsCapability 工具相关能力
type ToolsCapability struct {
	ListChanged bool `json:"listChanged,omitempty"` // 是否支持工具列表变更通知
}

// ==================== MCP 工具相关类型 ====================

// MCPTool MCP 工具定义
// 每个工具代表一个可被 LLM 调用的服务端功能
type MCPTool struct {
	Name        string      `json:"name"`                  // 工具名称 (唯一标识)
	Description string      `json:"description,omitempty"` // 工具用途描述
	InputSchema interface{} `json:"inputSchema"`           // 输入参数的 JSON Schema
}

// MCPToolsListResult tools/list 响应结果
type MCPToolsListResult struct {
	Tools []MCPTool `json:"tools"`
}

// MCPToolCallParams tools/call 请求参数
// 在标准 MCP 参数基础上，通过 _meta 字段扩展了服务治理元数据
//
// JSON 示例：
//
//	{
//	  "name": "get_weather",
//	  "arguments": {"city": "北京"},
//	  "_meta": {
//	    "tokens": 100,
//	    "method": "get_weather",
//	    "name": "client-1"
//	  }
//	}
type MCPToolCallParams struct {
	Name      string                 `json:"name"`                // 调用的工具名称
	Arguments map[string]interface{} `json:"arguments,omitempty"` // 工具输入参数
	Meta      *GovernanceMeta        `json:"_meta,omitempty"`     // 治理元数据 (MCP 标准扩展点)
}

// GovernanceMeta MCP 服务治理扩展元数据
// 嵌入在 JSON-RPC params._meta 中，在 MCP 请求间传递服务治理信息
//
// 核心思想：客户端在每次工具调用时携带"令牌"(tokens)作为预算，
// 服务端根据当前负载动态定价，当 tokens < price 时拒绝请求。
type GovernanceMeta struct {
	Tokens int64  `json:"tokens,omitempty"` // 请求携带的令牌数 (预算)
	Method string `json:"method,omitempty"` // 工具/方法标识 (用于价格路由)
	Name   string `json:"name,omitempty"`   // 发起方节点名称
}

// MCPToolCallResult tools/call 响应结果
// 服务端通过 _meta 将当前价格返回给客户端，供下次调用时参考
//
// JSON 示例（成功）：
//
//	{
//	  "content": [{"type": "text", "text": "北京今天晴，25°C"}],
//	  "_meta": {"price": "50", "name": "weather-server-1"}
//	}
type MCPToolCallResult struct {
	Content []ContentBlock `json:"content"`           // 工具返回的内容块
	IsError bool           `json:"isError,omitempty"` // 工具执行是否出错
	Meta    *ResponseMeta  `json:"_meta,omitempty"`   // 响应中的治理元数据
}

// ContentBlock MCP 内容块
type ContentBlock struct {
	Type string `json:"type"`           // 内容类型: "text", "image", "resource"
	Text string `json:"text,omitempty"` // 文本内容
}

// ResponseMeta 响应端治理元数据
// 服务端在返回中携带当前价格，客户端据此更新本地价格缓存
type ResponseMeta struct {
	Price string `json:"price,omitempty"` // 当前服务价格 (字符串形式)
	Name  string `json:"name,omitempty"`  // 服务端节点名称
}

// ==================== 辅助构造函数 ====================

// NewJSONRPCRequest 创建一个 JSON-RPC 2.0 请求
// params 会被自动序列化为 json.RawMessage
func NewJSONRPCRequest(id interface{}, method string, params interface{}) (*JSONRPCRequest, error) {
	var rawParams json.RawMessage
	if params != nil {
		b, err := json.Marshal(params)
		if err != nil {
			return nil, fmt.Errorf("序列化参数失败: %w", err)
		}
		rawParams = b
	}
	return &JSONRPCRequest{
		JSONRPC: JSONRPCVersion,
		ID:      id,
		Method:  method,
		Params:  rawParams,
	}, nil
}

// NewSuccessResponse 创建一个成功的 JSON-RPC 2.0 响应
func NewSuccessResponse(id interface{}, result interface{}) *JSONRPCResponse {
	return &JSONRPCResponse{
		JSONRPC: JSONRPCVersion,
		ID:      id,
		Result:  result,
	}
}

// NewErrorResponse 创建一个错误的 JSON-RPC 2.0 响应
// 当治理策略拒绝请求时，data 中通常包含 {"price": "...", "name": "..."}
func NewErrorResponse(id interface{}, code int, message string, data interface{}) *JSONRPCResponse {
	return &JSONRPCResponse{
		JSONRPC: JSONRPCVersion,
		ID:      id,
		Error: &RPCError{
			Code:    code,
			Message: message,
			Data:    data,
		},
	}
}

// TextContent 创建一个文本类型的内容块
func TextContent(text string) ContentBlock {
	return ContentBlock{Type: "text", Text: text}
}
