package plangate

import (
	"sync/atomic"
	"time"
)

type AdmissionState string

const (
	AdmissionGreen  AdmissionState = "green"
	AdmissionYellow AdmissionState = "yellow"
	AdmissionRed    AdmissionState = "red"
)

type AdaptiveAdmissionConfig struct {
	Enabled bool

	GreenIntensityMax      float64
	GreenCapLoadMax        float64
	GreenReActStep0LoadMax float64

	RedIntensityMin      float64
	RedCapLoadMin        float64
	RedReActStep0LoadMin float64

	RedCascadeRateMin          float64
	RedRecoverableErrorRateMin float64

	GreenWait  time.Duration
	YellowWait time.Duration
	RedWait    time.Duration
}

func DefaultAdaptiveAdmissionConfig() AdaptiveAdmissionConfig {
	return AdaptiveAdmissionConfig{
		Enabled: true,

		GreenIntensityMax:      0.20,
		GreenCapLoadMax:        0.70,
		GreenReActStep0LoadMax: 0.70,

		RedIntensityMin:      0.65,
		RedCapLoadMin:        0.90,
		RedReActStep0LoadMin: 0.90,

		RedCascadeRateMin:          0.15,
		RedRecoverableErrorRateMin: 0.20,

		GreenWait:  200 * time.Millisecond,
		YellowWait: 50 * time.Millisecond,
		RedWait:    0,
	}
}

func (s *MCPDPServer) currentSessionCapLoad() float64 {
	if s == nil || s.sessionCap == nil {
		return 0
	}
	capSlots := cap(s.sessionCap)
	if capSlots <= 0 {
		return 0
	}
	return float64(len(s.sessionCap)) / float64(capSlots)
}

func (s *MCPDPServer) currentReActStep0Load() float64 {
	if s == nil || s.reactStep0Limit <= 0 {
		return 0
	}
	inflight := atomic.LoadInt64(&s.reactStep0Inflight)
	if inflight <= 0 {
		return 0
	}
	return float64(inflight) / float64(s.reactStep0Limit)
}

func (s *MCPDPServer) recentCascadeRate() float64 {
	// TODO: integrate with runtime cascade-failure counters.
	return 0
}

func (s *MCPDPServer) recentRecoverableErrorRate() float64 {
	// TODO: integrate with runtime recoverable-error counters.
	return 0
}

func classifyAdmissionState(cfg AdaptiveAdmissionConfig, intensity, capLoad, reactStep0Load, cascadeRate, recoverableRate float64) AdmissionState {
	if intensity >= cfg.RedIntensityMin ||
		capLoad >= cfg.RedCapLoadMin ||
		reactStep0Load >= cfg.RedReActStep0LoadMin ||
		cascadeRate >= cfg.RedCascadeRateMin ||
		recoverableRate >= cfg.RedRecoverableErrorRateMin {
		return AdmissionRed
	}
	if intensity < cfg.GreenIntensityMax &&
		capLoad < cfg.GreenCapLoadMax &&
		reactStep0Load < cfg.GreenReActStep0LoadMax {
		return AdmissionGreen
	}
	return AdmissionYellow
}

func (s *MCPDPServer) admissionState() AdmissionState {
	if s == nil {
		return AdmissionGreen
	}
	cfg := s.adaptiveAdmission
	state := classifyAdmissionState(
		cfg,
		s.getGovernanceIntensity(),
		s.currentSessionCapLoad(),
		s.currentReActStep0Load(),
		s.recentCascadeRate(),
		s.recentRecoverableErrorRate(),
	)
	// TODO: add admission-state hysteresis if production traces show oscillation.
	return state
}

func (s *MCPDPServer) adaptiveStep0Wait(_ AgentMode, state AdmissionState) time.Duration {
	if s == nil || !s.adaptiveAdmission.Enabled {
		return s.sessionCapWait
	}
	switch state {
	case AdmissionGreen:
		return s.adaptiveAdmission.GreenWait
	case AdmissionYellow:
		return s.adaptiveAdmission.YellowWait
	case AdmissionRed:
		return s.adaptiveAdmission.RedWait
	default:
		return s.adaptiveAdmission.YellowWait
	}
}

func (s *MCPDPServer) recordAdmissionState(state AdmissionState) {
	if s == nil || !s.adaptiveAdmission.Enabled {
		return
	}
	switch state {
	case AdmissionGreen:
		atomic.AddInt64(&s.adaptiveGreenCount, 1)
	case AdmissionYellow:
		atomic.AddInt64(&s.adaptiveYellowCount, 1)
	case AdmissionRed:
		atomic.AddInt64(&s.adaptiveRedCount, 1)
	}
}
