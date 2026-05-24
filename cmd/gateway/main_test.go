package main

import (
	"testing"
	"time"

	"mcp-governance/plangate"
)

func TestBuildRecoveryCheckpointStoreInMemoryNoOp(t *testing.T) {
	store, err := buildRecoveryCheckpointStore(plangate.RecoveryConfig{
		Enabled:     true,
		TTL:         5 * time.Minute,
		MaxAttempts: 3,
		Store:       "inmemory",
	}, "127.0.0.1:6379")
	if err != nil {
		t.Fatalf("buildRecoveryCheckpointStore(inmemory): %v", err)
	}
	if store != nil {
		t.Fatalf("buildRecoveryCheckpointStore(inmemory) returned unexpected store: %T", store)
	}
}

func TestBuildRecoveryCheckpointStoreRedisFailsFastWhenUnavailable(t *testing.T) {
	_, err := buildRecoveryCheckpointStore(plangate.RecoveryConfig{
		Enabled:     true,
		TTL:         5 * time.Minute,
		MaxAttempts: 3,
		Store:       "redis",
	}, "127.0.0.1:1")
	if err == nil {
		t.Fatal("expected redis recovery checkpoint store construction to fail fast")
	}
}
