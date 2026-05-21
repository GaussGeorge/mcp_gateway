package plangate

// session_state_store_redis.go
//
// RedisSessionStateStore implements SessionStateStore using Redis for shared
// state across multiple PlanGate gateway nodes.
//
// Uses a minimal built-in RESP client (no external dependencies).
// A single persistent TCP connection is maintained; it is transparently
// re-established on error.
//
// Redis key layout:
//   pg:admit:{session_id}      – admission marker (1, TTL-bound)
//   pg:res:{session_id}        – JSON SharedPSRecord (TTL-bound)
//   pg:active_sessions         – global integer counter (INCR/DECR)
//
// Atomic admission uses a Lua script via EVAL to avoid TOCTOU races.

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"strconv"
	"strings"
	"sync"
	"time"
)

// ─────────────────────────────────────────────────────────────────────────────
// Minimal RESP client
// ─────────────────────────────────────────────────────────────────────────────

type respConn struct {
	addr string
	mu   sync.Mutex
	conn net.Conn
	r    *bufio.Reader
}

func newRespConn(addr string) *respConn {
	return &respConn{addr: addr}
}

// ensureConn (caller must hold mu).
func (c *respConn) ensureConn() error {
	if c.conn != nil {
		return nil
	}
	conn, err := net.DialTimeout("tcp", c.addr, 5*time.Second)
	if err != nil {
		return fmt.Errorf("redis dial %s: %w", c.addr, err)
	}
	c.conn = conn
	c.r = bufio.NewReader(conn)
	return nil
}

// Do executes a Redis command and returns the parsed response.
// On any I/O error the connection is closed and callers get a wrapped error.
func (c *respConn) Do(args ...string) (interface{}, error) {
	c.mu.Lock()
	defer c.mu.Unlock()

	if err := c.ensureConn(); err != nil {
		return nil, err
	}

	// Build RESP array
	var sb strings.Builder
	sb.WriteString(fmt.Sprintf("*%d\r\n", len(args)))
	for _, a := range args {
		sb.WriteString(fmt.Sprintf("$%d\r\n%s\r\n", len(a), a))
	}
	cmd := sb.String()

	if _, err := fmt.Fprint(c.conn, cmd); err != nil {
		c.conn.Close()
		c.conn = nil
		return nil, fmt.Errorf("redis write: %w", err)
	}

	val, err := readRESP(c.r)
	if err != nil {
		c.conn.Close()
		c.conn = nil
		return nil, err
	}
	return val, nil
}

// readRESP reads one RESP value from r.
func readRESP(r *bufio.Reader) (interface{}, error) {
	line, err := r.ReadString('\n')
	if err != nil {
		return nil, fmt.Errorf("redis read: %w", err)
	}
	line = strings.TrimRight(line, "\r\n")
	if len(line) == 0 {
		return nil, fmt.Errorf("redis: empty response line")
	}

	switch line[0] {
	case '+': // simple string
		return line[1:], nil
	case '-': // error
		return nil, fmt.Errorf("redis error: %s", line[1:])
	case ':': // integer
		n, err := strconv.ParseInt(line[1:], 10, 64)
		return n, err
	case '$': // bulk string
		n, err := strconv.ParseInt(line[1:], 10, 64)
		if err != nil {
			return nil, err
		}
		if n < 0 {
			return nil, nil // null bulk string
		}
		buf := make([]byte, n+2) // +2 for \r\n
		if _, err := io.ReadFull(r, buf); err != nil {
			return nil, fmt.Errorf("redis bulk read: %w", err)
		}
		return string(buf[:n]), nil
	case '*': // array
		count, err := strconv.ParseInt(line[1:], 10, 64)
		if err != nil {
			return nil, err
		}
		if count < 0 {
			return nil, nil // null array
		}
		arr := make([]interface{}, count)
		for i := range arr {
			arr[i], err = readRESP(r)
			if err != nil {
				return nil, err
			}
		}
		return arr, nil
	default:
		return nil, fmt.Errorf("redis: unknown type byte %q in %q", line[0], line)
	}
}

// respInt coerces an interface{} returned by Do to int64.
func respInt(v interface{}) int64 {
	switch x := v.(type) {
	case int64:
		return x
	case string:
		n, _ := strconv.ParseInt(x, 10, 64)
		return n
	}
	return 0
}

// ─────────────────────────────────────────────────────────────────────────────
// Lua scripts (sent via EVAL)
// ─────────────────────────────────────────────────────────────────────────────

// luaAdmit atomically performs:
//   1. Dedup check: if pg:admit:{sid} exists → return 2 (duplicate)
//   2. Cap check:   if pg:active_sessions >= max_slots → return 0 (cap full)
//   3. Admit:       SET pg:admit:{sid} 1 PX <ttl_ms>; INCR pg:active_sessions → return 1
//
// KEYS[1] = pg:admit:{session_id}
// KEYS[2] = pg:active_sessions
// ARGV[1] = max_slots (0 = unlimited)
// ARGV[2] = ttl_ms
// Returns array: [result_code, new_active_count]
const luaAdmit = `
local existing = redis.call('GET', KEYS[1])
if existing then
  return {2, tonumber(redis.call('GET', KEYS[2]) or '0')}
end
local max_s = tonumber(ARGV[1])
if max_s > 0 then
  local cur = tonumber(redis.call('GET', KEYS[2]) or '0')
  if cur >= max_s then
    return {0, cur}
  end
end
redis.call('SET', KEYS[1], '1', 'PX', ARGV[2])
local new_c = redis.call('INCR', KEYS[2])
return {1, new_c}
`

// luaRelease atomically:
//   1. If pg:admit:{sid} missing → return 0 (not found / already released)
//   2. DEL pg:admit:{sid}; DEL pg:res:{sid}; DECR pg:active_sessions (floor 0) → return 1
//
// KEYS[1] = pg:admit:{session_id}
// KEYS[2] = pg:active_sessions
// KEYS[3] = pg:res:{session_id}
// Returns array: [released, new_active_count]
const luaRelease = `
local existing = redis.call('GET', KEYS[1])
if not existing then
  return {0, tonumber(redis.call('GET', KEYS[2]) or '0')}
end
redis.call('DEL', KEYS[1])
redis.call('DEL', KEYS[3])
local new_c = tonumber(redis.call('DECR', KEYS[2]))
if new_c < 0 then
  redis.call('SET', KEYS[2], '0')
  new_c = 0
end
return {1, new_c}
`

// ─────────────────────────────────────────────────────────────────────────────
// RedisSessionStateStore
// ─────────────────────────────────────────────────────────────────────────────

// RedisSessionStateStore implements SessionStateStore backed by Redis.
// It is safe for concurrent use; all Redis operations go through a single
// serialised *respConn (one connection, mutex-protected).
//
// For experiments the throughput requirement is low (<<1k ops/s), so one
// connection is sufficient and avoids connection-pool complexity.
type RedisSessionStateStore struct {
	rc *respConn
}

// NewRedisSessionStateStore creates a store connecting to addr (host:port).
func NewRedisSessionStateStore(addr string) *RedisSessionStateStore {
	return &RedisSessionStateStore{rc: newRespConn(addr)}
}

func admitKey(sessionID string) string { return "pg:admit:" + sessionID }
func resKey(sessionID string) string   { return "pg:res:" + sessionID }

const activeKey = "pg:active_sessions"

func (s *RedisSessionStateStore) TryAdmitSession(_ context.Context, sessionID string, maxSlots int, ttl time.Duration) (TryAdmitResult, error) {
	ttlMs := ttl.Milliseconds()
	if ttlMs <= 0 {
		ttlMs = 60_000 // 60 s fallback
	}

	result, err := s.rc.Do(
		"EVAL", luaAdmit, "2",
		admitKey(sessionID), activeKey,
		strconv.Itoa(maxSlots),
		strconv.FormatInt(ttlMs, 10),
	)
	if err != nil {
		return AdmitNew, fmt.Errorf("TryAdmitSession: %w", err)
	}

	arr, ok := result.([]interface{})
	if !ok || len(arr) < 2 {
		return AdmitNew, fmt.Errorf("TryAdmitSession: unexpected result %v", result)
	}

	code := respInt(arr[0])
	switch code {
	case 0:
		return AdmitCapFull, nil
	case 1:
		return AdmitNew, nil
	case 2:
		return AdmitDuplicate, nil
	default:
		return AdmitNew, fmt.Errorf("TryAdmitSession: unknown code %d", code)
	}
}

func (s *RedisSessionStateStore) SaveReservation(_ context.Context, r *SharedPSRecord, ttl time.Duration) error {
	data, err := json.Marshal(r)
	if err != nil {
		return err
	}
	ttlMs := ttl.Milliseconds()
	if ttlMs <= 0 {
		ttlMs = 60_000
	}
	_, err = s.rc.Do("SET", resKey(r.SessionID), string(data), "PX", strconv.FormatInt(ttlMs, 10))
	return err
}

func (s *RedisSessionStateStore) GetReservation(_ context.Context, sessionID string) (*SharedPSRecord, error) {
	val, err := s.rc.Do("GET", resKey(sessionID))
	if err != nil {
		return nil, err
	}
	if val == nil {
		return nil, nil // not found
	}
	str, ok := val.(string)
	if !ok || str == "" {
		return nil, nil
	}
	var r SharedPSRecord
	if err := json.Unmarshal([]byte(str), &r); err != nil {
		return nil, fmt.Errorf("GetReservation decode: %w", err)
	}
	return &r, nil
}

func (s *RedisSessionStateStore) AdvanceReservationStep(_ context.Context, sessionID string) (int, int, bool, error) {
	// Get current reservation
	val, err := s.rc.Do("GET", resKey(sessionID))
	if err != nil {
		return 0, 0, false, err
	}
	if val == nil {
		return 0, 0, false, nil // not found
	}
	str, ok := val.(string)
	if !ok || str == "" {
		return 0, 0, false, nil
	}
	var r SharedPSRecord
	if err := json.Unmarshal([]byte(str), &r); err != nil {
		return 0, 0, false, fmt.Errorf("AdvanceReservationStep decode: %w", err)
	}

	// Increment step
	r.CurrentStep++
	complete := r.CurrentStep >= r.TotalSteps
	newStep := r.CurrentStep

	// Save updated record
	updatedData, err := json.Marshal(&r)
	if err != nil {
		return 0, 0, false, err
	}
	_, err = s.rc.Do("SET", resKey(sessionID), string(updatedData))
	if err != nil {
		return 0, 0, false, err
	}

	return newStep, r.TotalSteps, complete, nil
}

func (s *RedisSessionStateStore) ReleaseSession(_ context.Context, sessionID string) error {
	_, err := s.rc.Do(
		"EVAL", luaRelease, "3",
		admitKey(sessionID), activeKey, resKey(sessionID),
	)
	if err != nil && !errors.Is(err, io.EOF) {
		return fmt.Errorf("ReleaseSession: %w", err)
	}
	return nil
}

func (s *RedisSessionStateStore) GlobalActiveCount(_ context.Context) (int, error) {
	val, err := s.rc.Do("GET", activeKey)
	if err != nil {
		return 0, err
	}
	if val == nil {
		return 0, nil
	}
	str, _ := val.(string)
	n, _ := strconv.Atoi(str)
	return n, nil
}
