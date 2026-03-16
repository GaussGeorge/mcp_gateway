// protocol.go
// 无治理网关 - MCP (Model Context Protocol) 协议类型定义
// 基于 JSON-RPC 2.0 标准实现，直接转发所有请求，不做任何限流/定价/过载保护
package nogovernance

import (
	"encoding/json"
	"fmt"
)

// ==================== JSON-RPC 2.0 基础协议类型 ====================

// JSONRPCVersion JSON-RPC 协议版本号，固定为 "2.0"
const JSONRPCVersion = "2.0"

// JSON-RPC 2.0 标准错误码
const (
	CodeParseError     = -32700 // 解析错误
	CodeInvalidRequest = -32600 // 无效请求
	CodeMethodNotFound = -32601 // 方法未找到
	CodeInvalidParams  = -32602 // 无效参数
	CodeInternalError  = -32603 // 内部错误
)

// JSONRPCRequest JSON-RPC 2.0 请求对象
type JSONRPCRequest struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      interface{}     `json:"id,omitempty"`
	Method  string          `json:"method"`
	Params  json.RawMessage `json:"params,omitempty"`
}

// JSONRPCResponse JSON-RPC 2.0 响应对象
type JSONRPCResponse struct {
	JSONRPC string      `json:"jsonrpc"`
	ID      interface{} `json:"id"`
	Result  interface{} `json:"result,omitempty"`
	Error   *RPCError   `json:"error,omitempty"`
}

// RPCError JSON-RPC 2.0 错误对象
type RPCError struct {
	Code    int         `json:"code"`
	Message string      `json:"message"`
	Data    interface{} `json:"data,omitempty"`
}

// Error 实现 error 接口
func (e *RPCError) Error() string {
	return fmt.Sprintf("JSON-RPC error %d: %s", e.Code, e.Message)
}

// ==================== MCP 协议方法名常量 ====================

const (
	MethodInitialize = "initialize"
	MethodToolsList  = "tools/list"
	MethodToolsCall  = "tools/call"
	MethodPing       = "ping"
)

// ==================== MCP 初始化相关类型 ====================

// MCPInitializeParams initialize 请求参数
type MCPInitializeParams struct {
	ProtocolVersion string         `json:"protocolVersion"`
	ClientInfo      Implementation `json:"clientInfo"`
	Capabilities    interface{}    `json:"capabilities,omitempty"`
}

// MCPInitializeResult initialize 响应结果
type MCPInitializeResult struct {
	ProtocolVersion string             `json:"protocolVersion"`
	ServerInfo      Implementation     `json:"serverInfo"`
	Capabilities    ServerCapabilities `json:"capabilities"`
}

// Implementation 客户端/服务端实现信息
type Implementation struct {
	Name    string `json:"name"`
	Version string `json:"version"`
}

// ServerCapabilities 服务端能力声明
type ServerCapabilities struct {
	Tools *ToolsCapability `json:"tools,omitempty"`
}

// ToolsCapability 工具相关能力
type ToolsCapability struct {
	ListChanged bool `json:"listChanged,omitempty"`
}

// ==================== MCP 工具相关类型 ====================

// MCPTool MCP 工具定义
type MCPTool struct {
	Name        string      `json:"name"`
	Description string      `json:"description,omitempty"`
	InputSchema interface{} `json:"inputSchema"`
}

// MCPToolsListResult tools/list 响应结果
type MCPToolsListResult struct {
	Tools []MCPTool `json:"tools"`
}

// MCPToolCallParams tools/call 请求参数
// 保留 _meta 字段以保持协议兼容，但网关不会对其做任何治理处理
type MCPToolCallParams struct {
	Name      string                 `json:"name"`
	Arguments map[string]interface{} `json:"arguments,omitempty"`
	Meta      map[string]interface{} `json:"_meta,omitempty"` // 原样透传，不做解析
}

// MCPToolCallResult tools/call 响应结果
type MCPToolCallResult struct {
	Content []ContentBlock         `json:"content"`
	IsError bool                   `json:"isError,omitempty"`
	Meta    map[string]interface{} `json:"_meta,omitempty"` // 原样透传
}

// ContentBlock MCP 内容块
type ContentBlock struct {
	Type string `json:"type"`
	Text string `json:"text,omitempty"`
}

// ==================== 辅助构造函数 ====================

// NewJSONRPCRequest 创建一个 JSON-RPC 2.0 请求
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

// NewSuccessResponse 创建成功响应
func NewSuccessResponse(id interface{}, result interface{}) *JSONRPCResponse {
	return &JSONRPCResponse{
		JSONRPC: JSONRPCVersion,
		ID:      id,
		Result:  result,
	}
}

// NewErrorResponse 创建错误响应
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

// TextContent 创建文本类型的内容块
func TextContent(text string) ContentBlock {
	return ContentBlock{Type: "text", Text: text}
}
