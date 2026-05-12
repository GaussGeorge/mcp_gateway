package plangate

import "testing"

// ─────────────────────────────────────────────────────────────────────────────
// Tests: maxRecoverySlots
// ─────────────────────────────────────────────────────────────────────────────

func TestMaxRecoverySlots_Normal(t *testing.T) {
	// total=30, p0=10 → available=20; frac=0.4 → slots=8
	got := maxRecoverySlots(30, 10, 0.4)
	if got != 8 {
		t.Errorf("expected 8, got %d", got)
	}
}

func TestMaxRecoverySlots_NoHeadroom(t *testing.T) {
	// All slots used by P0 → 0 recovery slots.
	got := maxRecoverySlots(30, 30, 0.4)
	if got != 0 {
		t.Errorf("no headroom: expected 0, got %d", got)
	}
}

func TestMaxRecoverySlots_P0ExceedsTotal(t *testing.T) {
	// P0 > total (defensive): still returns 0.
	got := maxRecoverySlots(10, 15, 0.4)
	if got != 0 {
		t.Errorf("p0>total: expected 0, got %d", got)
	}
}

func TestMaxRecoverySlots_FracZero_Disabled(t *testing.T) {
	got := maxRecoverySlots(30, 0, 0.0)
	if got != 0 {
		t.Errorf("frac=0: expected 0, got %d", got)
	}
}

func TestMaxRecoverySlots_FracNegative_Disabled(t *testing.T) {
	got := maxRecoverySlots(30, 0, -0.5)
	if got != 0 {
		t.Errorf("frac<0: expected 0, got %d", got)
	}
}

func TestMaxRecoverySlots_FracAboveOne_ClampsTo100Pct(t *testing.T) {
	// frac=2.0 should be clamped to 1.0 → all available slots.
	got := maxRecoverySlots(20, 5, 2.0)
	if got != 15 {
		t.Errorf("frac>1: expected 15 (all available), got %d", got)
	}
}

func TestMaxRecoverySlots_FracOne_AllAvailable(t *testing.T) {
	got := maxRecoverySlots(20, 5, 1.0)
	if got != 15 {
		t.Errorf("frac=1: expected 15, got %d", got)
	}
}

func TestMaxRecoverySlots_FloorRounding(t *testing.T) {
	// total=10, p0=3 → available=7; frac=0.4 → 2.8 → floor=2
	got := maxRecoverySlots(10, 3, 0.4)
	if got != 2 {
		t.Errorf("floor rounding: expected 2, got %d", got)
	}
}

// ─────────────────────────────────────────────────────────────────────────────
// Tests: canAdmitRecovery
// ─────────────────────────────────────────────────────────────────────────────

func TestCanAdmitRecovery_BelowQuota_True(t *testing.T) {
	// maxRecoverySlots(30, 10, 0.4) = 8; activeRecovery=5 < 8 → admit.
	if !canAdmitRecovery(30, 10, 5, 0.4) {
		t.Error("expected true (below quota), got false")
	}
}

func TestCanAdmitRecovery_AtQuota_False(t *testing.T) {
	// maxRecoverySlots(30, 10, 0.4) = 8; activeRecovery=8 → do NOT admit.
	if canAdmitRecovery(30, 10, 8, 0.4) {
		t.Error("expected false (at quota), got true")
	}
}

func TestCanAdmitRecovery_AboveQuota_False(t *testing.T) {
	// More recovery sessions than quota → reject.
	if canAdmitRecovery(30, 10, 10, 0.4) {
		t.Error("expected false (above quota), got true")
	}
}

func TestCanAdmitRecovery_ZeroFrac_AlwaysFalse(t *testing.T) {
	// Recovery disabled by frac=0.
	if canAdmitRecovery(100, 0, 0, 0.0) {
		t.Error("frac=0: expected false (recovery disabled), got true")
	}
}

func TestCanAdmitRecovery_FullCapacity_False(t *testing.T) {
	// All slots occupied by P0 → no room for recovery.
	if canAdmitRecovery(20, 20, 0, 0.4) {
		t.Error("full capacity: expected false, got true")
	}
}

func TestCanAdmitRecovery_DefaultConfig(t *testing.T) {
	cfg := DefaultRecoveryQuotaConfig()
	// Sanity: DefaultRecoveryQuotaConfig returns 0.4.
	if cfg.MaxRecoveryFraction != 0.4 {
		t.Errorf("expected default fraction=0.4, got %v", cfg.MaxRecoveryFraction)
	}
	// Use it: total=100, p0=60, recovery=0, frac=0.4 → available=40, quota=16 → admit.
	if !canAdmitRecovery(100, 60, 0, cfg.MaxRecoveryFraction) {
		t.Error("expected true with default config and room available")
	}
}
