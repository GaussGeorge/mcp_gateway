package plangate

// session_state_store_inmemory.go
//
// InMemorySessionStateStore is a standalone in-memory implementation of
// SessionStateStore. It is provided for unit-testing and for future use when
// operators want a single-binary multi-instance setup without Redis.
//
// NOTE: In the current gateway, --plangate-state-store=inmemory (the default)
// results in sharedStateStore=nil on MCPDPServer, so the existing
// HTTPBudgetReservationManager and sessionCap channel paths are used
// without modification. InMemorySessionStateStore is only instantiated when
// explicitly constructed in tests or tooling.

import (
	"context"
	"encoding/json"
	"sync"
	"sync/atomic"
	"time"
)

// InMemorySessionStateStore implements SessionStateStore with in-process maps.
// It is NOT shared across processes; use RedisSessionStateStore for multi-node.
type InMemorySessionStateStore struct {
	mu           sync.Mutex
	admitted     map[string]time.Time     // sessionID → expiry
	reservations map[string][]byte        // sessionID → JSON-encoded SharedPSRecord
	activeCount  int64                    // atomic
	maxSlots     int
}

// NewInMemorySessionStateStore creates a ready-to-use store.
// maxSlots <= 0 means unlimited.
func NewInMemorySessionStateStore(maxSlots int) *InMemorySessionStateStore {
	s := &InMemorySessionStateStore{
		admitted:     make(map[string]time.Time),
		reservations: make(map[string][]byte),
		maxSlots:     maxSlots,
	}
	go s.cleanupLoop()
	return s
}

func (s *InMemorySessionStateStore) TryAdmitSession(_ context.Context, sessionID string, maxSlots int, ttl time.Duration) (TryAdmitResult, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	// dedup
	if exp, ok := s.admitted[sessionID]; ok && time.Now().Before(exp) {
		return AdmitDuplicate, nil
	}

	// cap
	cap := maxSlots
	if cap <= 0 {
		cap = s.maxSlots
	}
	current := atomic.LoadInt64(&s.activeCount)
	if cap > 0 && int(current) >= cap {
		return AdmitCapFull, nil
	}

	s.admitted[sessionID] = time.Now().Add(ttl)
	atomic.AddInt64(&s.activeCount, 1)
	return AdmitNew, nil
}

func (s *InMemorySessionStateStore) SaveReservation(_ context.Context, r *SharedPSRecord, _ time.Duration) error {
	data, err := json.Marshal(r)
	if err != nil {
		return err
	}
	s.mu.Lock()
	s.reservations[r.SessionID] = data
	s.mu.Unlock()
	return nil
}

func (s *InMemorySessionStateStore) GetReservation(_ context.Context, sessionID string) (*SharedPSRecord, error) {
	s.mu.Lock()
	data, ok := s.reservations[sessionID]
	s.mu.Unlock()
	if !ok {
		return nil, nil
	}
	var r SharedPSRecord
	if err := json.Unmarshal(data, &r); err != nil {
		return nil, err
	}
	return &r, nil
}

func (s *InMemorySessionStateStore) AdvanceReservationStep(_ context.Context, sessionID string) (int, int, bool, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	data, ok := s.reservations[sessionID]
	if !ok {
		return 0, 0, false, nil // not found is not an error
	}
	var r SharedPSRecord
	if err := json.Unmarshal(data, &r); err != nil {
		return 0, 0, false, err
	}

	r.CurrentStep++
	complete := r.CurrentStep >= r.TotalSteps
	newStep := r.CurrentStep

	// save updated record
	updatedData, err := json.Marshal(&r)
	if err != nil {
		return 0, 0, false, err
	}
	s.reservations[sessionID] = updatedData

	return newStep, r.TotalSteps, complete, nil
}

func (s *InMemorySessionStateStore) ReleaseSession(_ context.Context, sessionID string) error {
	s.mu.Lock()
	_, wasAdmitted := s.admitted[sessionID]
	delete(s.admitted, sessionID)
	delete(s.reservations, sessionID)
	s.mu.Unlock()
	if wasAdmitted {
		if v := atomic.AddInt64(&s.activeCount, -1); v < 0 {
			atomic.StoreInt64(&s.activeCount, 0)
		}
	}
	return nil
}

func (s *InMemorySessionStateStore) GlobalActiveCount(_ context.Context) (int, error) {
	return int(atomic.LoadInt64(&s.activeCount)), nil
}

func (s *InMemorySessionStateStore) cleanupLoop() {
	t := time.NewTicker(30 * time.Second)
	defer t.Stop()
	for range t.C {
		now := time.Now()
		s.mu.Lock()
		for sid, exp := range s.admitted {
			if now.After(exp) {
				delete(s.admitted, sid)
				delete(s.reservations, sid)
				if v := atomic.AddInt64(&s.activeCount, -1); v < 0 {
					atomic.StoreInt64(&s.activeCount, 0)
				}
			}
		}
		s.mu.Unlock()
	}
}
