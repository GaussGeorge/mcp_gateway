package baseline

import (
	"sync"
	"time"
)

type bucketState struct {
	tokens     float64
	rate       float64
	burst      int64
	lastRefill time.Time
}

// MemoryStateStore provides in-memory token buckets + in-flight counters + TTL cleanup.
type MemoryStateStore struct {
	mu       sync.Mutex
	buckets  map[string]*bucketState
	inflight map[string]int64
	lastSeen map[string]time.Time
}

func NewMemoryStateStore() *MemoryStateStore {
	return &MemoryStateStore{
		buckets:  make(map[string]*bucketState),
		inflight: make(map[string]int64),
		lastSeen: make(map[string]time.Time),
	}
}

func (s *MemoryStateStore) touchLocked(key string, now time.Time) {
	s.lastSeen[key] = now
}

func (s *MemoryStateStore) AllowToken(key string, rate float64, burst int64) bool {
	if burst <= 0 || rate <= 0 {
		return true
	}
	now := time.Now()

	s.mu.Lock()
	defer s.mu.Unlock()

	b, ok := s.buckets[key]
	if !ok {
		s.buckets[key] = &bucketState{
			tokens:     float64(burst - 1),
			rate:       rate,
			burst:      burst,
			lastRefill: now,
		}
		s.touchLocked(key, now)
		return true
	}

	if b.rate != rate || b.burst != burst {
		b.rate = rate
		b.burst = burst
		if b.tokens > float64(burst) {
			b.tokens = float64(burst)
		}
	}

	elapsed := now.Sub(b.lastRefill).Seconds()
	if elapsed > 0 {
		b.tokens += elapsed * b.rate
		if b.tokens > float64(b.burst) {
			b.tokens = float64(b.burst)
		}
		b.lastRefill = now
	}

	s.touchLocked(key, now)
	if b.tokens >= 1.0 {
		b.tokens -= 1.0
		return true
	}
	return false
}

func (s *MemoryStateStore) AcquireInFlight(key string, max int64) bool {
	if max <= 0 {
		return true
	}
	now := time.Now()

	s.mu.Lock()
	defer s.mu.Unlock()

	cur := s.inflight[key]
	if cur >= max {
		s.touchLocked(key, now)
		return false
	}
	s.inflight[key] = cur + 1
	s.touchLocked(key, now)
	return true
}

func (s *MemoryStateStore) ReleaseInFlight(key string) {
	now := time.Now()
	s.mu.Lock()
	defer s.mu.Unlock()

	cur := s.inflight[key]
	if cur <= 1 {
		delete(s.inflight, key)
		delete(s.lastSeen, key)
		return
	}
	s.inflight[key] = cur - 1
	s.touchLocked(key, now)
}

func (s *MemoryStateStore) InFlight(key string) int64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.inflight[key]
}

func (s *MemoryStateStore) CleanupExpired(ttl time.Duration) {
	if ttl <= 0 {
		return
	}
	cutoff := time.Now().Add(-ttl)

	s.mu.Lock()
	defer s.mu.Unlock()

	for key, ts := range s.lastSeen {
		if ts.After(cutoff) {
			continue
		}
		if s.inflight[key] > 0 {
			continue
		}
		delete(s.buckets, key)
		delete(s.inflight, key)
		delete(s.lastSeen, key)
	}
}

func (s *MemoryStateStore) StartCleanup(interval time.Duration, ttl time.Duration, stop <-chan struct{}) {
	if interval <= 0 {
		interval = 30 * time.Second
	}
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-t.C:
			s.CleanupExpired(ttl)
		case <-stop:
			return
		}
	}
}
