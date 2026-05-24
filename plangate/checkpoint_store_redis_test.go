package plangate

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"net"
	"sort"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

type fakeSimpleString string
type fakeBulkString string

type fakeRedisEntry struct {
	value     string
	expiresAt time.Time
}

type fakeRedisServer struct {
	ln    net.Listener
	mu    sync.Mutex
	kv    map[string]fakeRedisEntry
	sets  map[string]map[string]struct{}
	conns map[net.Conn]struct{}
	wg    sync.WaitGroup
}

func startFakeRedisServer(t *testing.T) *fakeRedisServer {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen fake redis: %v", err)
	}
	s := &fakeRedisServer{
		ln:    ln,
		kv:    make(map[string]fakeRedisEntry),
		sets:  make(map[string]map[string]struct{}),
		conns: make(map[net.Conn]struct{}),
	}
	s.wg.Add(1)
	go s.serve()
	t.Cleanup(func() {
		_ = s.ln.Close()
		s.closeAllConnections()
		s.wg.Wait()
	})
	return s
}

func (s *fakeRedisServer) Addr() string {
	return s.ln.Addr().String()
}

func (s *fakeRedisServer) serve() {
	defer s.wg.Done()
	for {
		conn, err := s.ln.Accept()
		if err != nil {
			return
		}
		s.wg.Add(1)
		go s.handleConn(conn)
	}
}

func (s *fakeRedisServer) handleConn(conn net.Conn) {
	defer s.wg.Done()
	s.mu.Lock()
	s.conns[conn] = struct{}{}
	s.mu.Unlock()
	defer func() {
		s.mu.Lock()
		delete(s.conns, conn)
		s.mu.Unlock()
	}()
	defer conn.Close()

	reader := bufio.NewReader(conn)
	writer := bufio.NewWriter(conn)
	for {
		value, err := readRESP(reader)
		if err != nil {
			return
		}
		array, ok := value.([]interface{})
		if !ok {
			_ = writeRedisError(writer, "ERR expected array command")
			_ = writer.Flush()
			continue
		}
		args := make([]string, len(array))
		for i, item := range array {
			str, ok := item.(string)
			if !ok {
				_ = writeRedisError(writer, "ERR unsupported argument type")
				_ = writer.Flush()
				continue
			}
			args[i] = str
		}
		resp, execErr := s.exec(args)
		if execErr != nil {
			_ = writeRedisError(writer, execErr.Error())
		} else {
			_ = writeRedisValue(writer, resp)
		}
		_ = writer.Flush()
	}
}

func (s *fakeRedisServer) closeAllConnections() {
	s.mu.Lock()
	conns := make([]net.Conn, 0, len(s.conns))
	for conn := range s.conns {
		conns = append(conns, conn)
	}
	s.mu.Unlock()
	for _, conn := range conns {
		_ = conn.Close()
	}
}

func writeRedisError(w *bufio.Writer, msg string) error {
	_, err := fmt.Fprintf(w, "-%s\r\n", msg)
	return err
}

func writeRedisValue(w *bufio.Writer, value interface{}) error {
	switch v := value.(type) {
	case nil:
		_, err := w.WriteString("$-1\r\n")
		return err
	case fakeSimpleString:
		_, err := fmt.Fprintf(w, "+%s\r\n", string(v))
		return err
	case fakeBulkString:
		_, err := fmt.Fprintf(w, "$%d\r\n%s\r\n", len(v), string(v))
		return err
	case int64:
		_, err := fmt.Fprintf(w, ":%d\r\n", v)
		return err
	case []string:
		if _, err := fmt.Fprintf(w, "*%d\r\n", len(v)); err != nil {
			return err
		}
		for _, item := range v {
			if _, err := fmt.Fprintf(w, "$%d\r\n%s\r\n", len(item), item); err != nil {
				return err
			}
		}
		return nil
	default:
		return fmt.Errorf("unsupported fake redis response type %T", value)
	}
}

func (s *fakeRedisServer) cleanupExpiredLocked() {
	now := time.Now()
	for key, entry := range s.kv {
		if !entry.expiresAt.IsZero() && !now.Before(entry.expiresAt) {
			delete(s.kv, key)
		}
	}
}

func (s *fakeRedisServer) exec(args []string) (interface{}, error) {
	if len(args) == 0 {
		return nil, fmt.Errorf("ERR empty command")
	}
	command := strings.ToUpper(args[0])

	s.mu.Lock()
	defer s.mu.Unlock()
	s.cleanupExpiredLocked()

	switch command {
	case "PING":
		return fakeSimpleString("PONG"), nil
	case "GET":
		if len(args) != 2 {
			return nil, fmt.Errorf("ERR wrong number of arguments for GET")
		}
		entry, ok := s.kv[args[1]]
		if !ok {
			return nil, nil
		}
		return fakeBulkString(entry.value), nil
	case "SET":
		if len(args) < 3 {
			return nil, fmt.Errorf("ERR wrong number of arguments for SET")
		}
		key := args[1]
		value := args[2]
		nx := false
		var ttl time.Time
		for i := 3; i < len(args); i++ {
			switch strings.ToUpper(args[i]) {
			case "NX":
				nx = true
			case "PX":
				if i+1 >= len(args) {
					return nil, fmt.Errorf("ERR PX requires milliseconds")
				}
				ms, err := strconv.ParseInt(args[i+1], 10, 64)
				if err != nil {
					return nil, fmt.Errorf("ERR invalid PX value")
				}
				ttl = time.Now().Add(time.Duration(ms) * time.Millisecond)
				i++
			default:
				return nil, fmt.Errorf("ERR unsupported SET option %s", args[i])
			}
		}
		if nx {
			if _, exists := s.kv[key]; exists {
				return nil, nil
			}
		}
		s.kv[key] = fakeRedisEntry{value: value, expiresAt: ttl}
		return fakeSimpleString("OK"), nil
	case "DEL":
		if len(args) < 2 {
			return nil, fmt.Errorf("ERR wrong number of arguments for DEL")
		}
		var deleted int64
		for _, key := range args[1:] {
			if _, ok := s.kv[key]; ok {
				delete(s.kv, key)
				deleted++
			}
		}
		return deleted, nil
	case "SADD":
		if len(args) < 3 {
			return nil, fmt.Errorf("ERR wrong number of arguments for SADD")
		}
		set := s.sets[args[1]]
		if set == nil {
			set = make(map[string]struct{})
			s.sets[args[1]] = set
		}
		var added int64
		for _, member := range args[2:] {
			if _, ok := set[member]; ok {
				continue
			}
			set[member] = struct{}{}
			added++
		}
		return added, nil
	case "SREM":
		if len(args) < 3 {
			return nil, fmt.Errorf("ERR wrong number of arguments for SREM")
		}
		set := s.sets[args[1]]
		var removed int64
		for _, member := range args[2:] {
			if _, ok := set[member]; ok {
				delete(set, member)
				removed++
			}
		}
		return removed, nil
	case "SMEMBERS":
		if len(args) != 2 {
			return nil, fmt.Errorf("ERR wrong number of arguments for SMEMBERS")
		}
		set := s.sets[args[1]]
		members := make([]string, 0, len(set))
		for member := range set {
			members = append(members, member)
		}
		sort.Strings(members)
		return members, nil
	case "EVAL":
		if len(args) < 4 {
			return nil, fmt.Errorf("ERR wrong number of arguments for EVAL")
		}
		script := strings.TrimSpace(args[1])
		numKeys, err := strconv.Atoi(args[2])
		if err != nil || numKeys < 0 {
			return nil, fmt.Errorf("ERR invalid EVAL key count")
		}
		if len(args) < 3+numKeys {
			return nil, fmt.Errorf("ERR malformed EVAL invocation")
		}
		keys := args[3 : 3+numKeys]
		argv := args[3+numKeys:]
		if script == strings.TrimSpace(luaReleaseCheckpointLock) {
			if len(keys) != 1 || len(argv) != 1 {
				return nil, fmt.Errorf("ERR unsupported checkpoint lock release invocation")
			}
			entry, ok := s.kv[keys[0]]
			if !ok || entry.value != argv[0] {
				return int64(0), nil
			}
			delete(s.kv, keys[0])
			return int64(1), nil
		}
		return nil, fmt.Errorf("ERR unsupported EVAL script")
	default:
		return nil, fmt.Errorf("ERR unsupported command %s", command)
	}
}

func newRedisTestStore(t *testing.T, addr string) *RedisCheckpointStore {
	t.Helper()
	store := NewRedisCheckpointStore(addr)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := store.Ping(ctx); err != nil {
		t.Fatalf("redis ping: %v", err)
	}
	return store
}

func TestRedisCheckpointStoreSaveLoadAcrossInstances(t *testing.T) {
	redis := startFakeRedisServer(t)
	storeA := newRedisTestStore(t, redis.Addr())
	storeB := newRedisTestStore(t, redis.Addr())
	ctx := context.Background()

	cp := newTestCheckpoint("redis-sess-1")
	cp.CurrentStep = 2
	cp.RecoveryAttempts = 1
	cp.ExpiresAt = time.Now().Add(5 * time.Minute)
	if err := storeA.Save(ctx, cp); err != nil {
		t.Fatalf("Save: %v", err)
	}

	loaded, err := storeB.Load(ctx, cp.SessionID)
	if err != nil {
		t.Fatalf("Load from second store: %v", err)
	}
	if loaded.SessionID != cp.SessionID {
		t.Fatalf("SessionID mismatch: got %q want %q", loaded.SessionID, cp.SessionID)
	}
	if loaded.CurrentStep != 2 || loaded.RecoveryAttempts != 1 {
		t.Fatalf("unexpected loaded checkpoint: %+v", loaded)
	}
}

func TestRedisCheckpointStoreUpdateAcrossInstances(t *testing.T) {
	redis := startFakeRedisServer(t)
	storeA := newRedisTestStore(t, redis.Addr())
	storeB := newRedisTestStore(t, redis.Addr())
	ctx := context.Background()

	cp := newTestCheckpoint("redis-sess-2")
	cp.CurrentStep = 1
	if err := storeA.Save(ctx, cp); err != nil {
		t.Fatalf("Save: %v", err)
	}

	err := storeB.Update(ctx, cp.SessionID, func(current *SessionCheckpoint) (*SessionCheckpoint, error) {
		current.CurrentStep++
		current.RecoveryAttempts++
		current.Status = StatusRecoveryQueued
		return current, nil
	})
	if err != nil {
		t.Fatalf("Update from second store: %v", err)
	}

	loaded, err := storeA.Load(ctx, cp.SessionID)
	if err != nil {
		t.Fatalf("Load after update: %v", err)
	}
	if loaded.CurrentStep != 2 {
		t.Fatalf("CurrentStep=%d want=2", loaded.CurrentStep)
	}
	if loaded.RecoveryAttempts != 1 {
		t.Fatalf("RecoveryAttempts=%d want=1", loaded.RecoveryAttempts)
	}
	if loaded.Status != StatusRecoveryQueued {
		t.Fatalf("Status=%q want=%q", loaded.Status, StatusRecoveryQueued)
	}
}

func TestRedisCheckpointStoreConcurrentUpdateNoLostProgress(t *testing.T) {
	redis := startFakeRedisServer(t)
	storeA := newRedisTestStore(t, redis.Addr())
	storeB := newRedisTestStore(t, redis.Addr())
	ctx := context.Background()

	cp := newTestCheckpoint("redis-sess-concurrent")
	cp.CurrentStep = 0
	cp.RecoveryAttempts = 0
	if err := storeA.Save(ctx, cp); err != nil {
		t.Fatalf("Save: %v", err)
	}

	const updates = 24
	var wg sync.WaitGroup
	wg.Add(updates)
	for i := 0; i < updates; i++ {
		go func(i int) {
			defer wg.Done()
			store := storeA
			if i%2 == 1 {
				store = storeB
			}
			if err := store.Update(ctx, cp.SessionID, func(current *SessionCheckpoint) (*SessionCheckpoint, error) {
				current.CurrentStep++
				current.RecoveryAttempts++
				return current, nil
			}); err != nil {
				t.Errorf("Update[%d]: %v", i, err)
			}
		}(i)
	}
	wg.Wait()

	loaded, err := storeA.Load(ctx, cp.SessionID)
	if err != nil {
		t.Fatalf("Load after concurrent updates: %v", err)
	}
	if loaded.CurrentStep != updates {
		t.Fatalf("CurrentStep=%d want=%d", loaded.CurrentStep, updates)
	}
	if loaded.RecoveryAttempts != updates {
		t.Fatalf("RecoveryAttempts=%d want=%d", loaded.RecoveryAttempts, updates)
	}
}

func TestRedisCheckpointStoreDeleteThenNotFound(t *testing.T) {
	redis := startFakeRedisServer(t)
	store := newRedisTestStore(t, redis.Addr())
	ctx := context.Background()

	cp := newTestCheckpoint("redis-sess-delete")
	if err := store.Save(ctx, cp); err != nil {
		t.Fatalf("Save: %v", err)
	}
	if err := store.Delete(ctx, cp.SessionID); err != nil {
		t.Fatalf("Delete: %v", err)
	}
	_, err := store.Load(ctx, cp.SessionID)
	if !errors.Is(err, ErrCheckpointNotFound) {
		t.Fatalf("Load after delete = %v, want ErrCheckpointNotFound", err)
	}
}

func TestRedisCheckpointStoreExpiredLoadAndCleanup(t *testing.T) {
	redis := startFakeRedisServer(t)
	store := newRedisTestStore(t, redis.Addr())
	ctx := context.Background()

	cp := newTestCheckpoint("redis-sess-expired")
	cp.ExpiresAt = time.Now().Add(25 * time.Millisecond)
	if err := store.Save(ctx, cp); err != nil {
		t.Fatalf("Save: %v", err)
	}
	time.Sleep(60 * time.Millisecond)

	_, err := store.Load(ctx, cp.SessionID)
	if !errors.Is(err, ErrCheckpointExpired) {
		t.Fatalf("Load after TTL = %v, want ErrCheckpointExpired", err)
	}
	_, err = store.Load(ctx, cp.SessionID)
	if !errors.Is(err, ErrCheckpointNotFound) {
		t.Fatalf("second Load after cleanup = %v, want ErrCheckpointNotFound", err)
	}
}

func TestRedisCheckpointStoreListRecoverableOrderingAndFiltering(t *testing.T) {
	redis := startFakeRedisServer(t)
	store := newRedisTestStore(t, redis.Addr())
	ctx := context.Background()
	now := time.Now()
	base := now.Add(-10 * time.Minute)

	records := []*SessionCheckpoint{
		{SessionID: "ccc", Mode: AgentModePlanSolve, Status: StatusCheckpointed, RecoveryAttempts: 2, CreatedAt: base.Add(1 * time.Minute)},
		{SessionID: "aaa", Mode: AgentModePlanSolve, Status: StatusCheckpointed, RecoveryAttempts: 0, CreatedAt: base.Add(3 * time.Minute)},
		{SessionID: "bbb", Mode: AgentModePlanSolve, Status: StatusRecoveryQueued, RecoveryAttempts: 0, CreatedAt: base.Add(1 * time.Minute)},
		{SessionID: "ddd", Mode: AgentModePlanSolve, Status: StatusRecoveryQueued, RecoveryAttempts: 1, CreatedAt: base.Add(2 * time.Minute)},
		{SessionID: "live", Mode: AgentModePlanSolve, Status: StatusActiveCheckpoint, RecoveryAttempts: 0, CreatedAt: base},
		{SessionID: "done", Mode: AgentModePlanSolve, Status: StatusSucceeded, RecoveryAttempts: 0, CreatedAt: base},
		{SessionID: "expired", Mode: AgentModePlanSolve, Status: StatusCheckpointed, RecoveryAttempts: 0, CreatedAt: base, ExpiresAt: now.Add(-1 * time.Minute)},
	}
	for _, record := range records {
		if err := store.Save(ctx, record); err != nil {
			t.Fatalf("Save %s: %v", record.SessionID, err)
		}
	}

	all, err := store.ListRecoverable(ctx, 0, now)
	if err != nil {
		t.Fatalf("ListRecoverable: %v", err)
	}
	if len(all) != 4 {
		t.Fatalf("ListRecoverable length=%d want=4", len(all))
	}
	wantOrder := []string{"bbb", "aaa", "ddd", "ccc"}
	for i, cp := range all {
		if cp.SessionID != wantOrder[i] {
			t.Fatalf("position %d = %q want %q", i, cp.SessionID, wantOrder[i])
		}
	}
}

func TestRedisCheckpointStoreUnavailablePing(t *testing.T) {
	store := NewRedisCheckpointStore("127.0.0.1:1")
	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	if err := store.Ping(ctx); err == nil {
		t.Fatal("Ping should fail when Redis is unavailable")
	}
}
