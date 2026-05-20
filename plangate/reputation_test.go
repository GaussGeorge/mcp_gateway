package plangate

import (
	"testing"
)

func TestReputationNewAgentScore(t *testing.T) {
	rm := NewReputationManager(DefaultReputationConfig())
	if s := rm.GetScore("agent-1"); s != 1.0 {
		t.Errorf("new agent score = %.3f, want 1.0", s)
	}
}

func TestReputationSuccessIncrease(t *testing.T) {
	rm := NewReputationManager(DefaultReputationConfig())
	rm.RecordBudgetViolation("agent-1") // drop to ~0.85
	rm.RecordBudgetViolation("agent-1") // drop to ~0.70
	before := rm.GetScore("agent-1")
	rm.RecordSuccess("agent-1")
	after := rm.GetScore("agent-1")
	if after <= before {
		t.Errorf("success should increase score: before=%.3f after=%.3f", before, after)
	}
}

func TestReputationViolationDecrease(t *testing.T) {
	rm := NewReputationManager(DefaultReputationConfig())
	rm.RecordBudgetViolation("agent-1")
	s := rm.GetScore("agent-1")
	if s >= 1.0 {
		t.Errorf("violation should decrease score: got %.3f", s)
	}
}

func TestReputationBan(t *testing.T) {
	cfg := DefaultReputationConfig()
	cfg.PenaltyRate = 0.3 // aggressive penalty for testing
	rm := NewReputationManager(cfg)

	if rm.IsBanned("agent-1") {
		t.Error("new agent should not be banned")
	}

	// Multiple violations to drop below threshold
	for i := 0; i < 10; i++ {
		rm.RecordBudgetViolation("agent-1")
	}
	if !rm.IsBanned("agent-1") {
		t.Errorf("agent should be banned after violations, score=%.3f", rm.GetScore("agent-1"))
	}
}

func TestReputationAdjustBudget(t *testing.T) {
	rm := NewReputationManager(DefaultReputationConfig())
	// Full reputation → full budget
	adj := rm.AdjustBudget("good-agent", 1000)
	if adj != 1000 {
		t.Errorf("full reputation budget = %d, want 1000", adj)
	}

	// Degraded reputation → proportional budget
	rm.RecordBudgetViolation("bad-agent")
	rm.RecordBudgetViolation("bad-agent")
	adj = rm.AdjustBudget("bad-agent", 1000)
	if adj >= 1000 {
		t.Errorf("degraded reputation budget should be < 1000, got %d", adj)
	}
	if adj <= 0 {
		t.Errorf("adjusted budget should be > 0, got %d", adj)
	}
}

func TestReputationValidateDAGLimits(t *testing.T) {
	cfg := DefaultReputationConfig()
	cfg.MaxDAGSteps = 5
	cfg.MaxBudgetPerReq = 500
	rm := NewReputationManager(cfg)

	// Valid plan
	plan := &HTTPDAGPlan{
		SessionID: "s1",
		Budget:    300,
		Steps: []HTTPDAGStep{
			{StepID: "s1", ToolName: "calculate"},
			{StepID: "s2", ToolName: "web_fetch", DependsOn: []string{"s1"}},
		},
	}
	if err := rm.ValidateDAGLimits(plan); err != nil {
		t.Errorf("valid plan should pass: %v", err)
	}

	// Too many steps
	manySteps := make([]HTTPDAGStep, 10)
	for i := range manySteps {
		manySteps[i] = HTTPDAGStep{StepID: "s" + string(rune('0'+i)), ToolName: "calculate"}
	}
	bigPlan := &HTTPDAGPlan{SessionID: "s2", Budget: 300, Steps: manySteps}
	if err := rm.ValidateDAGLimits(bigPlan); err == nil {
		t.Error("plan with too many steps should fail")
	}

	// Too high budget
	expensivePlan := &HTTPDAGPlan{
		SessionID: "s3",
		Budget:    10000,
		Steps:     []HTTPDAGStep{{StepID: "s1", ToolName: "calculate"}},
	}
	if err := rm.ValidateDAGLimits(expensivePlan); err == nil {
		t.Error("plan with excessive budget should fail")
	}
}

func TestReputationDisabled(t *testing.T) {
	cfg := DefaultReputationConfig()
	cfg.Enabled = false
	rm := NewReputationManager(cfg)

	// Ban check always returns false when disabled
	rm.RecordBudgetViolation("agent-1")
	rm.RecordBudgetViolation("agent-1")
	rm.RecordBudgetViolation("agent-1")
	rm.RecordBudgetViolation("agent-1")
	rm.RecordBudgetViolation("agent-1")
	if rm.IsBanned("agent-1") {
		t.Error("disabled reputation should never ban")
	}

	// Budget adjustment returns original when disabled
	adj := rm.AdjustBudget("agent-1", 1000)
	if adj != 1000 {
		t.Errorf("disabled reputation should return original budget, got %d", adj)
	}
}

func TestReputationStats(t *testing.T) {
	rm := NewReputationManager(DefaultReputationConfig())
	rm.RecordSuccess("agent-1")
	rm.RecordBudgetViolation("agent-2")

	stats := rm.Stats()
	if stats["total_agents"].(int) != 2 {
		t.Errorf("expected 2 agents, got %v", stats["total_agents"])
	}
}
