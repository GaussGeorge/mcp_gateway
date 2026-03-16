// config.go
// 静态限流网关 - 配置管理
// 支持从 JSON 配置文件加载限流参数，便于统一管理
package staticratelimit

import (
	"encoding/json"
	"fmt"
	"os"
)

// RateLimitConfig 静态限流网关配置
// MaxQPS: 每秒最大请求数 (固定阈值)
// BurstSize: 突发容量 (允许的瞬时峰值)，默认等于 MaxQPS
type RateLimitConfig struct {
	MaxQPS    float64 `json:"max_qps"`    // 每秒最大请求数，如 20.0
	BurstSize int     `json:"burst_size"` // 突发容量（令牌桶最大容量）
}

// DefaultConfig 返回默认配置：20 QPS，突发容量 20
func DefaultConfig() *RateLimitConfig {
	return &RateLimitConfig{
		MaxQPS:    20.0,
		BurstSize: 20,
	}
}

// LoadConfigFromFile 从 JSON 文件加载限流配置
//
// 配置文件格式示例 (config.json):
//
//	{
//	    "max_qps": 20,
//	    "burst_size": 20
//	}
//
// 若文件不存在或解析失败，返回错误
func LoadConfigFromFile(path string) (*RateLimitConfig, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("读取配置文件失败: %w", err)
	}

	cfg := DefaultConfig()
	if err := json.Unmarshal(data, cfg); err != nil {
		return nil, fmt.Errorf("解析配置文件失败: %w", err)
	}

	if cfg.MaxQPS <= 0 {
		return nil, fmt.Errorf("max_qps 必须大于 0，当前值: %f", cfg.MaxQPS)
	}
	if cfg.BurstSize <= 0 {
		cfg.BurstSize = int(cfg.MaxQPS)
	}

	return cfg, nil
}

// LoadConfigOrDefault 从文件加载配置，若失败则返回默认配置
// 适用于不强制要求配置文件的场景
func LoadConfigOrDefault(path string) *RateLimitConfig {
	cfg, err := LoadConfigFromFile(path)
	if err != nil {
		return DefaultConfig()
	}
	return cfg
}
