package plangate

import (
	"context"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func TestAdmissionStateGreen(t *testing.T) {
	cfg := DefaultAdaptiveAdmissionConfig()
	if got := classifyAdmissionState(cfg, 0.0, 0.1, 0.1, 0.0, 0.0); got != AdmissionGreen {
		t.Fatalf("expected green, got %s", got)
	}
}

func TestAdmissionStateYellow(t *testing.T) {
	cfg := DefaultAdaptiveAdmissionConfig()
	if got := classifyAdmissionState(cfg, 0.30, 0.1, 0.1, 0.0, 0.0); got != AdmissionYellow {
		t.Fatalf("expected yellow by intensity, got %s", got)
	}
}

func TestAdmissionStateRedByIntensity(t *testing.T) {
	cfg := DefaultAdaptiveAdmissionConfig()
	if got := classifyAdmissionState(cfg, 0.70, 0.1, 0.1, 0.0, 0.0); got != AdmissionRed {
		t.Fatalf("expected red by intensity, got %s", got)
	}
}

func TestAdmissionStateRedByCapLoad(t *testing.T) {
	cfg := DefaultAdaptiveAdmissionConfig()
	if got := classifyAdmissionState(cfg, 0.1, 0.95, 0.1, 0.0, 0.0); got != AdmissionRed {
		t.Fatalf("expected red by cap load, got %s", got)
	}
}

func TestAdaptiveWaitPolicy(t *testing.T) {
	s := makeTestServer(4)
	cfg := DefaultAdaptiveAdmissionConfig()
	s.SetAdaptiveAdmissionConfig(cfg)
	s.EnableAdaptiveAdmission(true)

	if got := s.adaptiveStep0Wait(AgentModePlanSolve, AdmissionGreen); got != 200*time.Millisecond {
		t.Fatalf("expected green wait 200ms, got %v", got)
	}
	if got := s.adaptiveStep0Wait(AgentModePlanSolve, AdmissionYellow); got != 50*time.Millisecond {
		t.Fatalf("expected yellow wait 50ms, got %v", got)
	}
	if got := s.adaptiveStep0Wait(AgentModePlanSolve, AdmissionRed); got != 0 {
		t.Fatalf("expected red wait 0, got %v", got)
	}
}

func TestPSBudgetRejectStillHardUnderGreen(t *testing.T) {
	s := makeTestServer(8)
	s.EnableAdaptiveAdmission(true)
	s.governor.SetOwnPrice(100)
	cfg := DefaultAdaptiveAdmissionConfig()
	cfg.GreenIntensityMax = 2.0
	cfg.RedIntensityMin = 10.0
	cfg.GreenCapLoadMax = 1.1
	cfg.GreenReActStep0LoadMax = 1.1
	cfg.RedCapLoadMin = 2.0
	cfg.RedReActStep0LoadMin = 2.0
	s.SetAdaptiveAdmissionConfig(cfg)

	dagJSON := makeDAGJSON(1, 10)
	req := makeToolCallRPC("calculate")
	httpReq := httptest.NewRequest("POST", "/", nil)

	resp := s.handlePlanAndSolveFirstStep(context.Background(), httptest.NewRecorder(), httpReq, req, dagJSON, "sess-budget")
	if resp.Error == nil {
		t.Fatal("expected budget reject, got success")
	}
	if got := atomic.LoadInt64(&s.step0RejectBudget); got == 0 {
		t.Fatal("expected budget reject counter > 0")
	}
}

func TestPSCapacityRelaxedUnderGreen(t *testing.T) {
	s := makeTestServer(1)
	s.EnableAdaptiveAdmission(true)
	cfg := DefaultAdaptiveAdmissionConfig()
	cfg.GreenWait = 200 * time.Millisecond
	cfg.YellowWait = 50 * time.Millisecond
	cfg.RedWait = 0
	cfg.GreenIntensityMax = 2.0
	cfg.RedIntensityMin = 10.0
	cfg.GreenCapLoadMax = 1.1
	cfg.GreenReActStep0LoadMax = 1.1
	cfg.RedCapLoadMin = 2.0
	cfg.RedReActStep0LoadMin = 2.0
	s.SetAdaptiveAdmissionConfig(cfg)

	s.sessionCap <- struct{}{}
	go func() {
		time.Sleep(60 * time.Millisecond)
		<-s.sessionCap
	}()
	dagJSON := makeDAGJSON(1, 10000)
	req := makeToolCallRPC("calculate")
	httpReq := httptest.NewRequest("POST", "/", nil)

	resp := s.handlePlanAndSolveFirstStep(context.Background(), httptest.NewRecorder(), httpReq, req, dagJSON, "sess-green-relax")
	if resp.Error == nil {
		return
	}
	t.Fatalf("expected admit under green wait relaxation, got error=%v", resp.Error)
}

func TestPSCapacityRejectedUnderRed(t *testing.T) {
	s := makeTestServer(1)
	s.EnableAdaptiveAdmission(true)
	cfg := DefaultAdaptiveAdmissionConfig()
	cfg.RedWait = 0
	s.SetAdaptiveAdmissionConfig(cfg)

	s.sessionCap <- struct{}{}
	dagJSON := makeDAGJSON(1, 10000)
	req := makeToolCallRPC("calculate")
	httpReq := httptest.NewRequest("POST", "/", nil)
	start := time.Now()
	resp := s.handlePlanAndSolveFirstStep(context.Background(), httptest.NewRecorder(), httpReq, req, dagJSON, "sess-red-reject")
	elapsed := time.Since(start)

	if resp.Error == nil {
		t.Fatal("expected reject under red full capacity")
	}
	if elapsed > 40*time.Millisecond {
		t.Fatalf("expected near-immediate red rejection, got %v", elapsed)
	}
	if got := atomic.LoadInt64(&s.step0RejectCapacity); got == 0 {
		t.Fatal("expected capacity reject counter > 0")
	}
	if got := atomic.LoadInt64(&s.step0RejectAdaptiveRed); got == 0 {
		t.Fatal("expected adaptive red reject counter > 0")
	}
}
