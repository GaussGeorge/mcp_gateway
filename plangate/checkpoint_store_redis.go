package plangate

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
	"time"
)

const (
	checkpointAllIndexKey         = "pg:cp:all"
	checkpointRecoverableIndexKey = "pg:cp:recoverable"
)

const luaReleaseCheckpointLock = `
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
`

func checkpointRedisKey(sessionID string) string {
	return "pg:cp:{" + sessionID + "}"
}

func checkpointLockRedisKey(sessionID string) string {
	return "pg:cp:lock:{" + sessionID + "}"
}

func isRecoverableCheckpointStatus(status SessionStatus) bool {
	return status == StatusCheckpointed || status == StatusRecoveryQueued
}

// RedisCheckpointStore persists PlanGate-R checkpoints into Redis using the
// minimal built-in RESP client shared with RedisSessionStateStore.
//
// Key layout:
//   - pg:cp:{session_id}           checkpoint JSON blob
//   - pg:cp:all                    set of every known checkpoint session id
//   - pg:cp:recoverable            set of recoverable checkpoint session ids
//   - pg:cp:lock:{session_id}      short-lived update lock for atomic Update
type RedisCheckpointStore struct {
	rc        *respConn
	lockTTL   time.Duration
	lockRetry time.Duration
}

// NewRedisCheckpointStore creates a Redis-backed checkpoint store.
func NewRedisCheckpointStore(addr string) *RedisCheckpointStore {
	return &RedisCheckpointStore{
		rc:        newRespConn(addr),
		lockTTL:   5 * time.Second,
		lockRetry: 10 * time.Millisecond,
	}
}

// Ping verifies that Redis is reachable. Gateway wiring uses this to fail fast
// instead of silently falling back to an in-memory checkpoint store.
func (s *RedisCheckpointStore) Ping(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	resp, err := s.rc.Do("PING")
	if err != nil {
		return err
	}
	if pong, ok := resp.(string); !ok || pong != "PONG" {
		return fmt.Errorf("unexpected redis PING response: %v", resp)
	}
	return nil
}

func checkpointTTLMillis(expiresAt, now time.Time) (int64, bool) {
	if expiresAt.IsZero() {
		return 0, false
	}
	ttlMs := expiresAt.Sub(now).Milliseconds()
	if ttlMs <= 0 {
		ttlMs = 1
	}
	return ttlMs, true
}

func materializeCheckpointForRedis(cp *SessionCheckpoint, now time.Time) (*SessionCheckpoint, []byte, error) {
	if cp == nil || cp.SessionID == "" {
		return nil, nil, ErrInvalidCheckpoint
	}
	clone := cp.Clone()
	if clone.CreatedAt.IsZero() {
		clone.CreatedAt = now
	}
	clone.UpdatedAt = now
	data, err := json.Marshal(clone)
	if err != nil {
		return nil, nil, err
	}
	return clone, data, nil
}

func respStringSlice(v interface{}) ([]string, error) {
	if v == nil {
		return nil, nil
	}
	items, ok := v.([]interface{})
	if !ok {
		return nil, fmt.Errorf("unexpected redis array response: %T", v)
	}
	out := make([]string, 0, len(items))
	for _, item := range items {
		str, ok := item.(string)
		if !ok {
			return nil, fmt.Errorf("unexpected redis array member type: %T", item)
		}
		out = append(out, str)
	}
	return out, nil
}

func (s *RedisCheckpointStore) savePreparedCheckpoint(cp *SessionCheckpoint, data []byte, now time.Time) error {
	ttlMs, withTTL := checkpointTTLMillis(cp.ExpiresAt, now)
	args := []string{"SET", checkpointRedisKey(cp.SessionID), string(data)}
	if withTTL {
		args = append(args, "PX", strconv.FormatInt(ttlMs, 10))
	}
	if _, err := s.rc.Do(args...); err != nil {
		return err
	}
	if _, err := s.rc.Do("SADD", checkpointAllIndexKey, cp.SessionID); err != nil {
		return err
	}
	if isRecoverableCheckpointStatus(cp.Status) {
		if _, err := s.rc.Do("SADD", checkpointRecoverableIndexKey, cp.SessionID); err != nil {
			return err
		}
		return nil
	}
	_, err := s.rc.Do("SREM", checkpointRecoverableIndexKey, cp.SessionID)
	return err
}

func (s *RedisCheckpointStore) sessionKnown(sessionID string) (bool, error) {
	members, err := s.rc.Do("SMEMBERS", checkpointAllIndexKey)
	if err != nil {
		return false, err
	}
	ids, err := respStringSlice(members)
	if err != nil {
		return false, err
	}
	for _, id := range ids {
		if id == sessionID {
			return true, nil
		}
	}
	return false, nil
}

func (s *RedisCheckpointStore) cleanupIndexes(sessionID string) {
	_, _ = s.rc.Do("SREM", checkpointAllIndexKey, sessionID)
	_, _ = s.rc.Do("SREM", checkpointRecoverableIndexKey, sessionID)
}

func (s *RedisCheckpointStore) loadCheckpoint(sessionID string, now time.Time) (*SessionCheckpoint, error) {
	val, err := s.rc.Do("GET", checkpointRedisKey(sessionID))
	if err != nil {
		return nil, err
	}
	if val == nil {
		known, knownErr := s.sessionKnown(sessionID)
		if knownErr != nil {
			return nil, knownErr
		}
		if known {
			s.cleanupIndexes(sessionID)
			return nil, ErrCheckpointExpired
		}
		return nil, ErrCheckpointNotFound
	}
	raw, ok := val.(string)
	if !ok || raw == "" {
		return nil, ErrCheckpointNotFound
	}
	var cp SessionCheckpoint
	if err := json.Unmarshal([]byte(raw), &cp); err != nil {
		return nil, fmt.Errorf("checkpoint decode for %s: %w", sessionID, err)
	}
	if cp.SessionID == "" {
		cp.SessionID = sessionID
	}
	if !cp.ExpiresAt.IsZero() && !now.Before(cp.ExpiresAt) {
		_ = s.Delete(context.Background(), sessionID)
		return nil, ErrCheckpointExpired
	}
	return cp.Clone(), nil
}

func (s *RedisCheckpointStore) acquireLock(ctx context.Context, sessionID string) (string, error) {
	lockKey := checkpointLockRedisKey(sessionID)
	lockToken := fmt.Sprintf("%d", time.Now().UnixNano())
	lockTTLms := s.lockTTL.Milliseconds()
	if lockTTLms <= 0 {
		lockTTLms = 5_000
	}

	for {
		if err := ctx.Err(); err != nil {
			return "", err
		}
		resp, err := s.rc.Do("SET", lockKey, lockToken, "NX", "PX", strconv.FormatInt(lockTTLms, 10))
		if err != nil {
			return "", err
		}
		if ok, _ := resp.(string); ok == "OK" {
			return lockToken, nil
		}

		timer := time.NewTimer(s.lockRetry)
		select {
		case <-ctx.Done():
			timer.Stop()
			return "", ctx.Err()
		case <-timer.C:
		}
	}
}

func (s *RedisCheckpointStore) releaseLock(sessionID, token string) {
	_, _ = s.rc.Do(
		"EVAL", luaReleaseCheckpointLock, "1",
		checkpointLockRedisKey(sessionID),
		token,
	)
}

// Save implements CheckpointStore.
func (s *RedisCheckpointStore) Save(ctx context.Context, cp *SessionCheckpoint) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	now := time.Now()
	prepared, data, err := materializeCheckpointForRedis(cp, now)
	if err != nil {
		return err
	}
	return s.savePreparedCheckpoint(prepared, data, now)
}

// Load implements CheckpointStore.
func (s *RedisCheckpointStore) Load(ctx context.Context, sessionID string) (*SessionCheckpoint, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return s.loadCheckpoint(sessionID, time.Now())
}

// Update implements CheckpointStore with a Redis-backed distributed lock so
// concurrent gateways do not interleave read-modify-write cycles.
func (s *RedisCheckpointStore) Update(
	ctx context.Context,
	sessionID string,
	fn func(*SessionCheckpoint) (*SessionCheckpoint, error),
) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	lockToken, err := s.acquireLock(ctx, sessionID)
	if err != nil {
		return err
	}
	defer s.releaseLock(sessionID, lockToken)

	current, err := s.loadCheckpoint(sessionID, time.Now())
	if err != nil {
		return err
	}
	modified, err := fn(current.Clone())
	if err != nil {
		return err
	}
	if modified == nil || modified.SessionID == "" || modified.SessionID != sessionID {
		return ErrInvalidCheckpoint
	}

	now := time.Now()
	prepared, data, err := materializeCheckpointForRedis(modified, now)
	if err != nil {
		return err
	}
	return s.savePreparedCheckpoint(prepared, data, now)
}

// Delete implements CheckpointStore.
func (s *RedisCheckpointStore) Delete(ctx context.Context, sessionID string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if _, err := s.rc.Do("DEL", checkpointRedisKey(sessionID)); err != nil {
		return err
	}
	if _, err := s.rc.Do("SREM", checkpointAllIndexKey, sessionID); err != nil {
		return err
	}
	_, err := s.rc.Do("SREM", checkpointRecoverableIndexKey, sessionID)
	return err
}

// ListRecoverable implements CheckpointStore.
func (s *RedisCheckpointStore) ListRecoverable(ctx context.Context, limit int, now time.Time) ([]*SessionCheckpoint, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	val, err := s.rc.Do("SMEMBERS", checkpointRecoverableIndexKey)
	if err != nil {
		return nil, err
	}
	ids, err := respStringSlice(val)
	if err != nil {
		return nil, err
	}

	candidates := make([]*SessionCheckpoint, 0, len(ids))
	for _, sessionID := range ids {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		cp, err := s.loadCheckpoint(sessionID, now)
		if err == ErrCheckpointExpired || err == ErrCheckpointNotFound {
			continue
		}
		if err != nil {
			return nil, err
		}
		if !isRecoverableCheckpointStatus(cp.Status) {
			_, _ = s.rc.Do("SREM", checkpointRecoverableIndexKey, sessionID)
			continue
		}
		candidates = append(candidates, cp)
	}

	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].RecoveryAttempts != candidates[j].RecoveryAttempts {
			return candidates[i].RecoveryAttempts < candidates[j].RecoveryAttempts
		}
		return candidates[i].CreatedAt.Before(candidates[j].CreatedAt)
	})
	if limit > 0 && len(candidates) > limit {
		candidates = candidates[:limit]
	}

	result := make([]*SessionCheckpoint, len(candidates))
	for i, cp := range candidates {
		result[i] = cp.Clone()
	}
	return result, nil
}

// Expire implements CheckpointStore.
func (s *RedisCheckpointStore) Expire(ctx context.Context, now time.Time) (int, error) {
	if err := ctx.Err(); err != nil {
		return 0, err
	}
	val, err := s.rc.Do("SMEMBERS", checkpointAllIndexKey)
	if err != nil {
		return 0, err
	}
	ids, err := respStringSlice(val)
	if err != nil {
		return 0, err
	}

	deleted := 0
	for _, sessionID := range ids {
		if err := ctx.Err(); err != nil {
			return deleted, err
		}
		cp, err := s.loadCheckpoint(sessionID, now)
		if err == ErrCheckpointExpired {
			deleted++
			continue
		}
		if err == ErrCheckpointNotFound {
			s.cleanupIndexes(sessionID)
			continue
		}
		if err != nil {
			return deleted, err
		}
		if !cp.ExpiresAt.IsZero() && !now.Before(cp.ExpiresAt) {
			if delErr := s.Delete(ctx, sessionID); delErr != nil {
				return deleted, delErr
			}
			deleted++
		}
	}
	return deleted, nil
}
