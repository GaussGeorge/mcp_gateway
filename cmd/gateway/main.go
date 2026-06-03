// cmd/gateway/main.go
// MCP 缃戝叧缁熶竴鍏ュ彛 锟?鏀寔 NG/SRL/DP/PlanGate 锟?Envoy/Kong 杩戜技鍩虹嚎
// 缃戝叧鎺ユ敹鍙戝帇鏈鸿锟?锟?搴旂敤娌荤悊閫昏緫 锟?浠ｇ悊锟?Python MCP 鍚庣
//
// 鐢ㄦ硶:
//
//	go run ./cmd/gateway --mode dp          --port 9003 --backend http://127.0.0.1:8080
//	go run ./cmd/gateway --mode dp-noregime --port 9004 --backend http://127.0.0.1:8080
//	go run ./cmd/gateway --mode ng          --port 9001 --backend http://127.0.0.1:8080
//	go run ./cmd/gateway --mode srl         --port 9002 --backend http://127.0.0.1:8080
//	go run ./cmd/gateway --mode envoy-approx --port 9006 --backend http://127.0.0.1:8080
//	go run ./cmd/gateway --mode kong-approx  --port 9007 --backend http://127.0.0.1:8080
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"sync/atomic"
	"time"

	mcpgov "mcp-governance"
	"mcp-governance/baseline"
	"mcp-governance/plangate"
)

// proxyOverloadDetector tracks in-flight proxy calls and updates ownPrice.
type proxyOverloadDetector struct {
	gov          *mcpgov.MCPGovernor
	activeCount  int64 // atomic: 褰撳墠娲昏穬鐨勫苟鍙戣姹傛暟
	interval     time.Duration
	currentPrice int64   // 褰撳墠浠锋牸 (detector 鍐呴儴璺熻釜)
	smoothActive float64 // 鎸囨暟骞虫粦鍚庣殑骞跺彂鏁帮紙鎻愪緵"璁板繂"锛岀敤浜庡畾浠凤級
	regimeSignal float64 // 瀵圭О杞诲害骞虫粦骞跺彂鏁帮紙鐢ㄤ簬 regime 妫€娴嬶級
}

func (d *proxyOverloadDetector) onRequestStart() {
	atomic.AddInt64(&d.activeCount, 1)
}

func (d *proxyOverloadDetector) onRequestEnd() {
	atomic.AddInt64(&d.activeCount, -1)
}

func (d *proxyOverloadDetector) run() {
	for range time.Tick(d.interval) {
		active := float64(atomic.LoadInt64(&d.activeCount))

		// Asymmetric smoothing: react quickly to spikes, recover slowly.
		if active > d.smoothActive {
			d.smoothActive = 0.7*d.smoothActive + 0.3*active
		} else {
			d.smoothActive = 0.99*d.smoothActive + 0.01*active
		}

		// Symmetric smoothing for regime-detection signal.
		d.regimeSignal = 0.8*d.regimeSignal + 0.2*active

		// Feed regime signal into adaptive profile selection.
		d.gov.ApplyAdaptiveProfileSignal(d.regimeSignal)

		// 浠庡綋鍓嶆椿璺冩。浣嶅姩鎬佽鍙栨娴嬪櫒鍙傛暟
		priceStep, decayStep, maxConc := d.gov.GetDetectorParams()

		diff := int64(d.smoothActive) - maxConc

		if diff > 0 {
			d.currentPrice = diff * priceStep
		} else if d.currentPrice > 0 {
			d.currentPrice -= decayStep
			if d.currentPrice < 0 {
				d.currentPrice = 0
			}
		}

		d.gov.SetOwnPrice(d.currentPrice)
	}
}

func main() {
	mode := flag.String("mode", "dp", "缃戝叧妯″紡: ng | srl | dp | dp-noregime | envoy-approx | kong-approx")
	port := flag.Int("port", 9003, "缃戝叧鐩戝惉绔彛")
	backendURL := flag.String("backend", "http://127.0.0.1:8080", "Python MCP 鍚庣鍦板潃")
	host := flag.String("host", "127.0.0.1", "缃戝叧缁戝畾鍦板潃")

	// Multi-gateway experiment flags
	nodeID := flag.String("node-id", "", "Gateway node ID for X-Gateway-Node header (default host:port)")
	stateStore := flag.String("plangate-state-store", "inmemory",
		"PlanGate session state store backend (inmemory|redis)")
	redisAddr := flag.String("plangate-redis-addr", "127.0.0.1:6379",
		"Redis address used when --plangate-state-store=redis")

	// SRL 鍙傛暟
	srlQPS := flag.Float64("srl-qps", 50, "SRL: 浠ょ墝妗堕€熺巼 (req/s)")
	srlBurst := flag.Int64("srl-burst", 100, "SRL: token-bucket burst")
	srlMaxConc := flag.Int64("srl-max-conc", 20, "SRL: 鏈€澶у苟鍙戣繛鎺ユ暟")

	// Proxy approximation parameters (Envoy/Kong style).
	proxyGlobalQPS := flag.Float64("proxy-global-qps", 65, "ProxyApprox: global QPS")
	proxyGlobalBurst := flag.Int64("proxy-global-burst", 400, "ProxyApprox: 鍏ㄥ眬 burst")
	proxyMaxConc := flag.Int64("proxy-max-conc", 55, "ProxyApprox: global max concurrency")
	proxyRouteQPS := flag.Float64("proxy-route-qps", 35, "EnvoyApprox: route 锟?QPS")
	proxyRouteBurst := flag.Int64("proxy-route-burst", 100, "EnvoyApprox: route 锟?burst")
	proxyRouteMaxConc := flag.Int64("proxy-route-max-conc", 20, "EnvoyApprox: route max concurrency")
	kongSessionQPS := flag.Float64("kong-session-qps", 2, "KongApprox: per-consumer/session QPS")
	kongSessionBurst := flag.Int64("kong-session-burst", 5, "KongApprox: per-consumer/session burst")
	kongSessionTTL := flag.Int("kong-session-ttl", 300, "KongApprox: per-consumer key TTL (锟?")

	// Rajomon 鍙傛暟
	rajomonPriceStep := flag.Int64("rajomon-price-step", 100, "Rajomon: 杩囪浇娑ㄤ环姝ラ暱")

	// Rajomon+SB 鍙傛暟
	rajomonSBPriceStep := flag.Int64("rajomon-sb-price-step", 100, "Rajomon+SB: 杩囪浇娑ㄤ环姝ラ暱")

	// DAGOR 鍙傛暟
	dagorRTTThreshold := flag.Float64("dagor-rtt-threshold", 200.0, "DAGOR: RTT 杩囪浇妫€娴嬮槇锟?(ms)")
	dagorPriceStep := flag.Int64("dagor-price-step", 50, "DAGOR: 杩囪浇鏃朵紭鍏堢骇闂ㄦ姣忚疆澧為噺")

	// SBAC 鍙傛暟
	sbacMaxSessions := flag.Int64("sbac-max-sessions", 50, "SBAC: 鏈€澶у苟鍙戜細璇濇暟")

	// PP (Progress-Priority) 鍙傛暟
	ppMaxSessions := flag.Int64("pp-max-sessions", 50, "PP: 鏈€澶у苟鍙戜細璇濇暟")

	// PlanGate (MCPDP) 鍙傛暟
	plangateMaxSessions := flag.Int("plangate-max-sessions", 30,
		"PlanGate (Full): 骞跺彂浼氳瘽涓婇檺锟?=0 琛ㄧず涓嶉檺鍒讹級")
	plangatePriceStep := flag.Int64("plangate-price-step", 40,
		"PlanGate: 杩囪浇娑ㄤ环姝ラ暱")
	plangateSunkCostAlpha := flag.Float64("plangate-sunk-cost-alpha", 0.5,
		"PlanGate: ReAct 娌夋病鎴愭湰绯绘暟 (0=绂佺敤)")
	plangateSunkBetaDefault := 1.0
	if envBeta := os.Getenv("SUNK_BETA"); envBeta != "" {
		if v, err := fmt.Sscanf(envBeta, "%f", &plangateSunkBetaDefault); v != 1 || err != nil {
			log.Fatalf("SUNK_BETA 鐜鍙橀噺鏍煎紡閿欒: %s", envBeta)
		}
	}
	plangateSunkBeta := flag.Float64("plangate-sunk-beta", plangateSunkBetaDefault,
		"PlanGate: ReAct continuation pricing 璋冨埗绯绘暟 beta (1.0=榛樿, 绛変环鏃у叕锟?2-I(t)); 涔熷彲锟?SUNK_BETA 鐜鍙橀噺")
	plangateSessionCapWait := flag.Int("plangate-session-cap-wait", 0,
		"PlanGate: Session Cap 鎺掗槦绛夊緟瓒呮椂 (锟?, 0=绔嬪嵆鎷掔粷")
	plangateAdaptiveAdmission := flag.Bool("plangate-adaptive-admission", true,
		"PlanGate: enable adaptive capacity Step-0 admission (legacy alias)")
	adaptiveAdmission := flag.Bool("adaptive-admission", true,
		"PlanGate: enable adaptive capacity Step-0 admission")
	adaptiveGreenWaitMs := flag.Int("adaptive-green-wait-ms", 200,
		"PlanGate adaptive admission green-state session-cap wait in milliseconds")
	adaptiveYellowWaitMs := flag.Int("adaptive-yellow-wait-ms", 50,
		"PlanGate adaptive admission yellow-state session-cap wait in milliseconds")
	adaptiveRedWaitMs := flag.Int("adaptive-red-wait-ms", 0,
		"PlanGate adaptive admission red-state session-cap wait in milliseconds")
	adaptiveGreenIntensity := flag.Float64("adaptive-green-intensity", 0.20,
		"PlanGate adaptive admission green threshold for governance intensity")
	adaptiveRedIntensity := flag.Float64("adaptive-red-intensity", 0.65,
		"PlanGate adaptive admission red threshold for governance intensity")
	plangateDisableCapacityStep0 := flag.Bool("plangate-disable-capacity-step0", false,
		"PlanGate: 绂佺敤 capacity/overload Step-0 reject锛堜繚鐣欓锟?DAG/瀹夊叏/閲嶅纭嫆缁濓級")
	commitmentTokenMode := flag.String("commitment-token-mode", "optional",
		"PlanGate Commitment Token mode: off|optional|strict")
	commitmentTokenSecret := flag.String("commitment-token-secret", "",
		"PlanGate Commitment Token HMAC secret; defaults to PLANGATE_COMMITMENT_SECRET or an auto-generated local secret")
	commitmentTokenTTL := flag.Duration("commitment-token-ttl", 0,
		"PlanGate Commitment Token TTL; 0 follows the reservation TTL")
	planAmendmentMode := flag.String("plan-amendment-mode", "recovery-only",
		"PlanGate delta-plan amendment mode: off|recovery-only")
	planAmendmentMaxCount := flag.Int("plan-amendment-max-count", 3,
		"PlanGate delta-plan amendment max accepted amendments per session")
	planAmendmentMaxBudgetDelta := flag.Int64("plan-amendment-max-budget-delta", 0,
		"PlanGate delta-plan amendment max allowed budget delta")
	planAmendmentRequireCommitment := flag.Bool("plan-amendment-require-commitment", true,
		"PlanGate delta-plan amendment requires a valid commitment token")
	plangateDiscountFunc := flag.String("plangate-discount-func", "quadratic",
		"PlanGate: 娌夋病鎴愭湰鎶樻墸鍑芥暟 (quadratic|linear|exponential|logarithmic)")

	// PlanGate-R recovery flags (Phase 3, default disabled)
	// All gateway modes behave identically to pre-Phase-3 when --enable-recovery is not set.
	enableRecovery := flag.Bool("enable-recovery", false,
		"PlanGate-R: 鍚敤 checkpoint recovery (榛樿 false; Phase 3 瀹為獙鎬у姛锟?")
	recoveryTTL := flag.Duration("recovery-ttl", 300*time.Second,
		"PlanGate-R: checkpoint 杩囨湡鏃堕棿 (榛樿 300s)")
	recoveryMaxAttempts := flag.Int("recovery-max-attempts", 3,
		"PlanGate-R: 鏈€澶ф仮澶嶅皾璇曟锟?(榛樿 3)")
	recoveryStore := flag.String("recovery-store", "inmemory",
		"PlanGate-R: checkpoint 瀛樺偍鍚庣 (Phase 3 浠呮敮锟?inmemory)")
	reactRecovery := flag.Bool("react-recovery", true,
		"PlanGate-R: enable ReAct client-cooperative recovery when --enable-recovery=true")
	realRateLimitMax := flag.Float64("real-ratelimit-max", 200,
		"PlanGate-Real: API 閰嶉涓婇檺 (GLM-4-Flash=200)")
	realLatencyThreshold := flag.Float64("real-latency-threshold", 5000,
		"PlanGate-Real: P95 寤惰繜闃堬拷?(ms), 杈惧埌姝ゅ€兼椂寤惰繜鍘嬪姏=1.0")

	flag.Parse()

	adaptiveEnabled := *adaptiveAdmission && *plangateAdaptiveAdmission
	adaptiveCfg := plangate.DefaultAdaptiveAdmissionConfig()
	adaptiveCfg.Enabled = adaptiveEnabled
	adaptiveCfg.GreenIntensityMax = *adaptiveGreenIntensity
	adaptiveCfg.RedIntensityMin = *adaptiveRedIntensity
	if *adaptiveGreenWaitMs >= 0 {
		adaptiveCfg.GreenWait = time.Duration(*adaptiveGreenWaitMs) * time.Millisecond
	}
	if *adaptiveYellowWaitMs >= 0 {
		adaptiveCfg.YellowWait = time.Duration(*adaptiveYellowWaitMs) * time.Millisecond
	}
	if *adaptiveRedWaitMs >= 0 {
		adaptiveCfg.RedWait = time.Duration(*adaptiveRedWaitMs) * time.Millisecond
	}

	effectiveCommitmentSecret := *commitmentTokenSecret
	if effectiveCommitmentSecret == "" {
		effectiveCommitmentSecret = os.Getenv("PLANGATE_COMMITMENT_SECRET")
	}
	if *commitmentTokenMode == string(plangate.CommitmentTokenModeStrict) &&
		*stateStore == "redis" && effectiveCommitmentSecret == "" {
		log.Fatalf("--commitment-token-mode=strict with --plangate-state-store=redis requires --commitment-token-secret or PLANGATE_COMMITMENT_SECRET")
	}
	commitmentCfg := plangate.CommitmentTokenConfig{
		Mode:   plangate.CommitmentTokenMode(*commitmentTokenMode),
		Secret: effectiveCommitmentSecret,
		TTL:    *commitmentTokenTTL,
	}
	amendmentPolicy := plangate.AmendmentPolicy{
		Mode:              plangate.AmendmentMode(*planAmendmentMode),
		MaxCount:          *planAmendmentMaxCount,
		MaxBudgetDelta:    *planAmendmentMaxBudgetDelta,
		RequireCommitment: *planAmendmentRequireCommitment,
	}

	// PlanGate-R: validate recovery-store early so we fail fast before binding ports.
	if *enableRecovery {
		switch *recoveryStore {
		case "inmemory", "redis":
		default:
			log.Fatalf("--recovery-store=%q is not supported (expected \"inmemory\" or \"redis\")", *recoveryStore)
		}
		if *recoveryStore == "redis" && *redisAddr == "" {
			log.Fatalf("--recovery-store=redis requires --plangate-redis-addr")
		}
	}
	tools, err := fetchBackendTools(*backendURL)
	if err != nil {
		log.Fatalf("鏃犳硶杩炴帴鍚庣 %s: %v", *backendURL, err)
	}
	log.Printf("fetched %d tools from backend", len(tools))

	var handler http.Handler

	switch *mode {
	case "ng":
		handler = setupNG(tools, *backendURL)
	case "srl":
		handler = setupSRL(tools, *backendURL, *srlQPS, *srlBurst, *srlMaxConc)
	case "envoy-approx":
		handler = setupEnvoyApprox(tools, *backendURL,
			*proxyGlobalQPS, *proxyGlobalBurst, *proxyMaxConc,
			*proxyRouteQPS, *proxyRouteBurst, *proxyRouteMaxConc)
	case "kong-approx":
		handler = setupKongApprox(tools, *backendURL,
			*proxyGlobalQPS, *proxyGlobalBurst,
			*kongSessionQPS, *kongSessionBurst,
			time.Duration(*kongSessionTTL)*time.Second)
	case "dp":
		handler = setupDP(tools, *backendURL)
	case "dp-noregime":
		handler = setupDPNoRegime(tools, *backendURL)
	case "mcpdp":
		handler = setupMCPDPVariant(tools, *backendURL, mcpdpVariant{
			name: "plangate-full", priceStep: *plangatePriceStep,
			maxConcurrentSessions: *plangateMaxSessions,
			disableBudgetLock:     false,
			adaptiveAdmission:     adaptiveEnabled,
			adaptiveAdmissionCfg:  adaptiveCfg,
			disableCapacityStep0:  *plangateDisableCapacityStep0,
			reactRecoveryEnabled:  *reactRecovery,
			sunkCostAlpha:         *plangateSunkCostAlpha,
			sunkCostBeta:          *plangateSunkBeta,
			discountFunc:          *plangateDiscountFunc,
			recoveryConfig:        buildRecoveryConfig(*enableRecovery, *recoveryTTL, *recoveryMaxAttempts, *recoveryStore),
			commitmentTokenConfig: commitmentCfg,
			amendmentPolicy:       amendmentPolicy,
			nodeID:                resolveNodeID(*nodeID, *host, *port),
			stateStoreType:        *stateStore,
			redisAddr:             *redisAddr,
		})
	case "mcpdp-no-budgetlock":
		handler = setupMCPDPVariant(tools, *backendURL, mcpdpVariant{
			name: "plangate-wo-budgetlock", priceStep: *plangatePriceStep,
			maxConcurrentSessions: *plangateMaxSessions,
			disableBudgetLock:     true,
			adaptiveAdmission:     adaptiveEnabled,
			adaptiveAdmissionCfg:  adaptiveCfg,
			disableCapacityStep0:  *plangateDisableCapacityStep0,
			reactRecoveryEnabled:  *reactRecovery,
			sunkCostAlpha:         *plangateSunkCostAlpha,
			sunkCostBeta:          *plangateSunkBeta,
			discountFunc:          *plangateDiscountFunc,
			recoveryConfig:        buildRecoveryConfig(*enableRecovery, *recoveryTTL, *recoveryMaxAttempts, *recoveryStore),
			commitmentTokenConfig: commitmentCfg,
			amendmentPolicy:       amendmentPolicy,
			nodeID:                resolveNodeID(*nodeID, *host, *port),
			stateStoreType:        *stateStore,
			redisAddr:             *redisAddr,
		})
	case "mcpdp-no-sessioncap":
		handler = setupMCPDPVariant(tools, *backendURL, mcpdpVariant{
			name: "plangate-wo-sessioncap", priceStep: *plangatePriceStep,
			maxConcurrentSessions: 0,
			disableBudgetLock:     false,
			adaptiveAdmission:     adaptiveEnabled,
			adaptiveAdmissionCfg:  adaptiveCfg,
			disableCapacityStep0:  *plangateDisableCapacityStep0,
			reactRecoveryEnabled:  *reactRecovery,
			sunkCostAlpha:         *plangateSunkCostAlpha,
			sunkCostBeta:          *plangateSunkBeta,
			discountFunc:          *plangateDiscountFunc,
			recoveryConfig:        buildRecoveryConfig(*enableRecovery, *recoveryTTL, *recoveryMaxAttempts, *recoveryStore),
			commitmentTokenConfig: commitmentCfg,
			amendmentPolicy:       amendmentPolicy,
			nodeID:                resolveNodeID(*nodeID, *host, *port),
			stateStoreType:        *stateStore,
			redisAddr:             *redisAddr,
		})
	case "mcpdp-adaptive":
		handler = setupMCPDPVariant(tools, *backendURL, mcpdpVariant{
			name: "plangate-adaptive", priceStep: *plangatePriceStep,
			maxConcurrentSessions: *plangateMaxSessions,
			disableBudgetLock:     false,
			adaptiveAdmission:     true,
			adaptiveAdmissionCfg:  adaptiveCfg,
			disableCapacityStep0:  false,
			reactRecoveryEnabled:  *reactRecovery,
			sunkCostAlpha:         *plangateSunkCostAlpha,
			sunkCostBeta:          *plangateSunkBeta,
			discountFunc:          *plangateDiscountFunc,
			recoveryConfig:        buildRecoveryConfig(*enableRecovery, *recoveryTTL, *recoveryMaxAttempts, *recoveryStore),
			commitmentTokenConfig: commitmentCfg,
			amendmentPolicy:       amendmentPolicy,
			nodeID:                resolveNodeID(*nodeID, *host, *port),
			stateStoreType:        *stateStore,
			redisAddr:             *redisAddr,
		})
	case "mcpdp-no-capacity-step0":
		handler = setupMCPDPVariant(tools, *backendURL, mcpdpVariant{
			name: "plangate-no-capacity-step0", priceStep: *plangatePriceStep,
			maxConcurrentSessions: *plangateMaxSessions,
			disableBudgetLock:     false,
			adaptiveAdmission:     false,
			adaptiveAdmissionCfg:  adaptiveCfg,
			disableCapacityStep0:  true,
			reactRecoveryEnabled:  *reactRecovery,
			sunkCostAlpha:         *plangateSunkCostAlpha,
			sunkCostBeta:          *plangateSunkBeta,
			discountFunc:          *plangateDiscountFunc,
			recoveryConfig:        buildRecoveryConfig(*enableRecovery, *recoveryTTL, *recoveryMaxAttempts, *recoveryStore),
			commitmentTokenConfig: commitmentCfg,
			amendmentPolicy:       amendmentPolicy,
			nodeID:                resolveNodeID(*nodeID, *host, *port),
			stateStoreType:        *stateStore,
			redisAddr:             *redisAddr,
		})
	case "mcpdp-real":
		handler = setupMCPDPReal(tools, *backendURL, mcpdpVariant{
			name: "plangate-real", priceStep: *plangatePriceStep,
			maxConcurrentSessions: *plangateMaxSessions,
			sunkCostAlpha:         *plangateSunkCostAlpha,
			adaptiveAdmission:     adaptiveEnabled,
			adaptiveAdmissionCfg:  adaptiveCfg,
			disableCapacityStep0:  *plangateDisableCapacityStep0,
			reactRecoveryEnabled:  *reactRecovery,
			sunkCostBeta:          *plangateSunkBeta,
			sessionCapWait:        time.Duration(*plangateSessionCapWait) * time.Second,
			discountFunc:          *plangateDiscountFunc,
			recoveryConfig:        buildRecoveryConfig(*enableRecovery, *recoveryTTL, *recoveryMaxAttempts, *recoveryStore),
			commitmentTokenConfig: commitmentCfg,
			amendmentPolicy:       amendmentPolicy,
			nodeID:                resolveNodeID(*nodeID, *host, *port),
			stateStoreType:        *stateStore,
			redisAddr:             *redisAddr,
		}, *realRateLimitMax, *realLatencyThreshold)
	case "mcpdp-real-no-sessioncap":
		handler = setupMCPDPReal(tools, *backendURL, mcpdpVariant{
			name: "plangate-real-wo-sessioncap", priceStep: *plangatePriceStep,
			maxConcurrentSessions: 0,
			sunkCostAlpha:         *plangateSunkCostAlpha,
			adaptiveAdmission:     adaptiveEnabled,
			adaptiveAdmissionCfg:  adaptiveCfg,
			disableCapacityStep0:  *plangateDisableCapacityStep0,
			reactRecoveryEnabled:  *reactRecovery,
			sunkCostBeta:          *plangateSunkBeta,
			sessionCapWait:        time.Duration(*plangateSessionCapWait) * time.Second,
			discountFunc:          *plangateDiscountFunc,
			recoveryConfig:        buildRecoveryConfig(*enableRecovery, *recoveryTTL, *recoveryMaxAttempts, *recoveryStore),
			commitmentTokenConfig: commitmentCfg,
			amendmentPolicy:       amendmentPolicy,
			nodeID:                resolveNodeID(*nodeID, *host, *port),
			stateStoreType:        *stateStore,
			redisAddr:             *redisAddr,
		}, *realRateLimitMax, *realLatencyThreshold)
	case "rajomon":
		handler = setupRajomon(tools, *backendURL, *rajomonPriceStep)
	case "rajomon-session":
		handler = setupRajomonSession(tools, *backendURL, *rajomonSBPriceStep)
	case "dagor":
		handler = setupDagor(tools, *backendURL, *dagorRTTThreshold, *dagorPriceStep)
	case "sbac":
		handler = setupSBAC(tools, *backendURL, *sbacMaxSessions)
	case "pp":
		handler = setupPP(tools, *backendURL, *ppMaxSessions)
	default:
		log.Fatalf("鏈煡妯″紡: %s (鍙拷? ng, srl, envoy-approx, kong-approx, dp, dp-noregime, mcpdp, mcpdp-adaptive, mcpdp-no-capacity-step0, mcpdp-no-budgetlock, mcpdp-no-sessioncap, mcpdp-real, mcpdp-real-no-sessioncap, rajomon, rajomon-session, dagor, sbac, pp)", *mode)
	}

	addr := fmt.Sprintf("%s:%d", *host, *port)
	log.Printf("========================================")
	log.Printf("  MCP Gateway [%s] 鍚姩", *mode)
	log.Printf("  鐩戝惉: http://%s", addr)
	log.Printf("  鍚庣: %s", *backendURL)
	log.Printf("========================================")

	server := &http.Server{
		Addr:         addr,
		Handler:      handler,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 120 * time.Second,
	}

	if err := server.ListenAndServe(); err != nil {
		log.Fatalf("鏈嶅姟鍚姩澶辫触: %v", err)
	}
}

// fetchBackendTools 锟?Python MCP 鍚庣鑾峰彇宸叉敞鍐岀殑宸ュ叿鍒楄〃
func fetchBackendTools(backendURL string) ([]mcpgov.MCPTool, error) {
	reqBody := mcpgov.JSONRPCRequest{
		JSONRPC: "2.0",
		ID:      "init-list",
		Method:  "tools/list",
	}
	body, _ := json.Marshal(reqBody)

	resp, err := http.Post(backendURL, "application/json", bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("杩炴帴鍚庣澶辫触: %w", err)
	}
	defer resp.Body.Close()

	data, _ := io.ReadAll(resp.Body)

	var rpcResp struct {
		Result struct {
			Tools []struct {
				Name        string      `json:"name"`
				Description string      `json:"description"`
				InputSchema interface{} `json:"inputSchema"`
			} `json:"tools"`
		} `json:"result"`
		Error *mcpgov.RPCError `json:"error"`
	}
	if err := json.Unmarshal(data, &rpcResp); err != nil {
		return nil, fmt.Errorf("瑙ｆ瀽鍚庣鍝嶅簲澶辫触: %w", err)
	}
	if rpcResp.Error != nil {
		return nil, fmt.Errorf("鍚庣杩斿洖閿欒: %s", rpcResp.Error.Message)
	}

	tools := make([]mcpgov.MCPTool, len(rpcResp.Result.Tools))
	for i, t := range rpcResp.Result.Tools {
		tools[i] = mcpgov.MCPTool{
			Name:        t.Name,
			Description: t.Description,
			InputSchema: t.InputSchema,
		}
	}
	return tools, nil
}

// makeProxyHandler creates a tool handler that proxies requests to the backend.
func makeProxyHandler(backendURL string, toolName string, detector *proxyOverloadDetector) mcpgov.ToolCallHandler {
	proxyTransport := &http.Transport{
		MaxIdleConns:        256,
		MaxIdleConnsPerHost: 128,
		MaxConnsPerHost:     0, // unlimited
		IdleConnTimeout:     90 * time.Second,
	}
	client := &http.Client{Timeout: 120 * time.Second, Transport: proxyTransport}

	return func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
		if detector != nil {
			detector.onRequestStart()
			defer detector.onRequestEnd()
		}
		// 鏋勫缓鍙戝線鍚庣锟?JSON-RPC 璇锋眰
		rpcReq := mcpgov.JSONRPCRequest{
			JSONRPC: "2.0",
			ID:      fmt.Sprintf("proxy-%s-%d", toolName, time.Now().UnixNano()),
			Method:  "tools/call",
		}
		paramsBytes, _ := json.Marshal(map[string]interface{}{
			"name":      params.Name,
			"arguments": params.Arguments,
		})
		rpcReq.Params = paramsBytes

		body, _ := json.Marshal(rpcReq)

		req, err := http.NewRequestWithContext(ctx, http.MethodPost, backendURL, bytes.NewReader(body))
		if err != nil {
			return nil, fmt.Errorf("鍒涘缓鍚庣璇锋眰澶辫触: %w", err)
		}
		req.Header.Set("Content-Type", "application/json")

		resp, err := client.Do(req)
		if err != nil {
			return nil, fmt.Errorf("鍚庣璋冪敤澶辫触: %w", err)
		}
		defer resp.Body.Close()

		data, _ := io.ReadAll(resp.Body)

		var rpcResp struct {
			Result *struct {
				Content []mcpgov.ContentBlock `json:"content"`
				Meta    *struct {
					Tool      string  `json:"tool"`
					Category  string  `json:"category"`
					LatencyMs float64 `json:"latency_ms"`
				} `json:"_meta"`
			} `json:"result"`
			Error *mcpgov.RPCError `json:"error"`
		}
		if err := json.Unmarshal(data, &rpcResp); err != nil {
			return nil, fmt.Errorf("瑙ｆ瀽鍚庣鍝嶅簲澶辫触: %w", err)
		}
		if rpcResp.Error != nil {
			return nil, fmt.Errorf("鍚庣宸ュ叿鎵ц閿欒: %s", rpcResp.Error.Message)
		}
		if rpcResp.Result == nil {
			return nil, fmt.Errorf("backend returned empty result")
		}

		return &mcpgov.MCPToolCallResult{
			Content: rpcResp.Result.Content,
		}, nil
	}
}

// === 缃戝叧鍒濆锟?===

func setupNG(tools []mcpgov.MCPTool, backendURL string) http.Handler {
	gw := baseline.NewNGGateway("ng-gateway")
	for _, tool := range tools {
		gw.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, nil))
		log.Printf("  [NG] 娉ㄥ唽宸ュ叿: %s", tool.Name)
	}
	return gw
}

func setupSRL(tools []mcpgov.MCPTool, backendURL string, qps float64, burst, maxConc int64) http.Handler {
	gw := baseline.NewSRLGateway("srl-gateway", baseline.SRLConfig{
		QPS:            qps,
		BurstSize:      burst,
		MaxConcurrency: maxConc,
	})
	for _, tool := range tools {
		gw.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, nil))
		log.Printf("  [SRL] 娉ㄥ唽宸ュ叿: %s", tool.Name)
	}
	return gw
}

func setupEnvoyApprox(tools []mcpgov.MCPTool, backendURL string,
	globalQPS float64, globalBurst, globalMaxConc int64,
	routeQPS float64, routeBurst, routeMaxConc int64) http.Handler {

	gw := baseline.NewEnvoyApproxGateway("envoy-approx-gateway", baseline.EnvoyApproxConfig{
		GlobalQPS:     globalQPS,
		GlobalBurst:   globalBurst,
		GlobalMaxConc: globalMaxConc,
		RouteQPS:      routeQPS,
		RouteBurst:    routeBurst,
		RouteMaxConc:  routeMaxConc,
	})

	for _, tool := range tools {
		gw.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, nil))
		log.Printf("  [EnvoyApprox] 娉ㄥ唽宸ュ叿: %s", tool.Name)
	}
	return gw
}

func setupKongApprox(tools []mcpgov.MCPTool, backendURL string,
	globalQPS float64, globalBurst int64,
	sessionQPS float64, sessionBurst int64, sessionTTL time.Duration) http.Handler {

	gw := baseline.NewKongApproxGateway("kong-approx-gateway", baseline.KongApproxConfig{
		GlobalQPS:    globalQPS,
		GlobalBurst:  globalBurst,
		SessionQPS:   sessionQPS,
		SessionBurst: sessionBurst,
		SessionTTL:   sessionTTL,
	})

	for _, tool := range tools {
		gw.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, nil))
		log.Printf("  [KongApprox] 娉ㄥ唽宸ュ叿: %s", tool.Name)
	}
	return gw
}

func setupDP(tools []mcpgov.MCPTool, backendURL string) http.Handler {
	// Build callMap where each tool has no downstream dependencies.
	callMap := make(map[string][]string)
	for _, tool := range tools {
		callMap[tool.Name] = []string{}
	}

	opts := map[string]interface{}{
		"initprice":             int64(0),
		"rateLimiting":          false,
		"loadShedding":          true,
		"pinpointQueuing":       false, // 鍙嶅悜浠ｇ悊鏋舵瀯锟?Go scheduler delay 鏃犳晥
		"latencyThreshold":      500 * time.Microsecond,
		"priceStep":             int64(180),
		"priceStrategy":         "expdecay",
		"priceDecayStep":        int64(1),
		"priceSensitivity":      int64(10000),
		"maxToken":              int64(20),
		"smoothingWindow":       5,
		"integralThreshold":     0.5,
		"priceUpdateRate":       5 * time.Millisecond,
		"tokenUpdateRate":       100 * time.Millisecond,
		"tokenUpdateStep":       int64(1),
		"tokenRefillDist":       "fixed",
		"priceAggregation":      "maximal",
		"enableAdaptiveProfile": true,
		// Regime-detection parameters.
		"regimeWindow":          100,
		"regimeVarianceLow":     1.0,
		"regimeVarianceHigh":    4.0,
		"regimeSpikeThreshold":  2.0,
		"profileSwitchCooldown": 500 * time.Millisecond,
			"burstyProfile": map[string]interface{}{
				"DetectorMaxConc": int64(200),
			},
			"periodicProfile": map[string]interface{}{
				"DetectorMaxConc": int64(200),
			},
			"steadyProfile": map[string]interface{}{
				"DetectorMaxConc": int64(200),
			},
			"toolWeights": map[string]int64{
			"mock_heavy": 5, // 閲嶉噺宸ュ叿鏉冮噸涔樻暟 (800ms vs ~100ms 锟?8:1)
		},
	}

	gov := mcpgov.NewMCPGovernor("dp-gateway", callMap, opts)
	server := mcpgov.NewMCPServer("dp-gateway", gov)

	// 鍒涘缓浠ｇ悊绾ц繃杞芥娴嬪櫒锛堝弬鏁颁粠 governor 褰撳墠妗ｄ綅鍔ㄦ€佽鍙栵級
	detector := &proxyOverloadDetector{
		gov:      gov,
		interval: 10 * time.Millisecond,
	}
	go detector.run()

	for _, tool := range tools {
		server.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, detector))
		log.Printf("  [DP] 娉ㄥ唽宸ュ叿: %s", tool.Name)
	}
	return server
}

func setupDPNoRegime(tools []mcpgov.MCPTool, backendURL string) http.Handler {
	callMap := make(map[string][]string)
	for _, tool := range tools {
		callMap[tool.Name] = []string{}
	}

	// Same as DP-Full but with adaptive profile switching disabled.
	opts := map[string]interface{}{
		"initprice":             int64(0),
		"rateLimiting":          false,
		"loadShedding":          true,
		"pinpointQueuing":       false, // 鍙嶅悜浠ｇ悊鏋舵瀯锟?Go scheduler delay 鏃犳晥
		"latencyThreshold":      500 * time.Microsecond,
		"priceStep":             int64(180),
		"priceStrategy":         "expdecay",
		"priceDecayStep":        int64(1),
		"priceSensitivity":      int64(10000),
		"maxToken":              int64(20),
		"smoothingWindow":       5,
		"integralThreshold":     0.5,
		"priceUpdateRate":       5 * time.Millisecond,
		"tokenUpdateRate":       100 * time.Millisecond,
		"tokenUpdateStep":       int64(1),
		"tokenRefillDist":       "fixed",
		"priceAggregation":      "maximal",
		"enableAdaptiveProfile": false, // 鍏抽敭宸紓锛氱鐢ㄨ嚜閫傚簲妗ｄ綅
		// 锟?DP-Full 鐩稿悓锟?Regime 鍙傛暟锛堜繚璇佸姣斿叕骞虫€э級
		"regimeWindow":          100,
		"regimeVarianceLow":     1.0,
		"regimeVarianceHigh":    4.0,
		"regimeSpikeThreshold":  2.0,
		"profileSwitchCooldown": 500 * time.Millisecond,
			"burstyProfile": map[string]interface{}{
				"DetectorMaxConc": int64(200),
			},
			"periodicProfile": map[string]interface{}{
				"DetectorMaxConc": int64(200),
			},
			"steadyProfile": map[string]interface{}{
				"DetectorMaxConc": int64(200),
			},
			"toolWeights": map[string]int64{
			"mock_heavy": 5,
		},
	}

	gov := mcpgov.NewMCPGovernor("dp-noregime-gateway", callMap, opts)
	server := mcpgov.NewMCPServer("dp-noregime-gateway", gov)

	// Same overload detector shape as DP-Full, but profile is fixed.
	detector := &proxyOverloadDetector{
		gov:      gov,
		interval: 10 * time.Millisecond,
	}
	go detector.run()

	for _, tool := range tools {
		server.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, detector))
		log.Printf("  [DP-NoRegime] 娉ㄥ唽宸ュ叿: %s", tool.Name)
	}
	return server
}

// mcpdpVariant configures PlanGate gateway variants used by experiments.
type mcpdpVariant struct {
	name                  string
	priceStep             int64
	maxConcurrentSessions int
	disableBudgetLock     bool
	adaptiveAdmission     bool
	adaptiveAdmissionCfg  plangate.AdaptiveAdmissionConfig
	disableCapacityStep0  bool
	reactRecoveryEnabled  bool
	sunkCostAlpha         float64
	sunkCostBeta          float64
	sessionCapWait        time.Duration
	discountFunc          string
	recoveryConfig        plangate.RecoveryConfig
	commitmentTokenConfig plangate.CommitmentTokenConfig
	amendmentPolicy       plangate.AmendmentPolicy
	// Multi-gateway experiment fields
	nodeID         string
	stateStoreType string
	redisAddr      string
}

// resolveNodeID returns nodeID if non-empty, otherwise "host:port".
func resolveNodeID(nodeID, host string, port int) string {
	if nodeID != "" {
		return nodeID
	}
	return fmt.Sprintf("%s:%d", host, port)
}

func setupMCPDPVariant(tools []mcpgov.MCPTool, backendURL string, v mcpdpVariant) http.Handler {
	callMap := make(map[string][]string)
	for _, tool := range tools {
		callMap[tool.Name] = []string{}
	}

	opts := map[string]interface{}{
		"initprice":             int64(0),
		"rateLimiting":          false,
		"loadShedding":          true,
		"pinpointQueuing":       false,
		"latencyThreshold":      10 * time.Second,
		"priceStep":             v.priceStep,
		"priceStrategy":         "expdecay",
		"priceDecayStep":        int64(1),
		"priceSensitivity":      int64(10000),
		"maxToken":              int64(20),
		"smoothingWindow":       5,
		"integralThreshold":     0.5,
		"priceUpdateRate":       5 * time.Millisecond,
		"tokenUpdateRate":       100 * time.Millisecond,
		"tokenUpdateStep":       int64(1),
		"tokenRefillDist":       "fixed",
		"priceAggregation":      "maximal",
		"enableAdaptiveProfile": true,
		"regimeWindow":          100,
		"regimeVarianceLow":     1.0,
		"regimeVarianceHigh":    4.0,
		"regimeSpikeThreshold":  2.0,
		"profileSwitchCooldown": 500 * time.Millisecond,
			"burstyProfile": map[string]interface{}{
				"DetectorMaxConc": int64(200),
			},
			"periodicProfile": map[string]interface{}{
				"DetectorMaxConc": int64(200),
			},
			"steadyProfile": map[string]interface{}{
				"DetectorMaxConc": int64(200),
			},
			"toolWeights": map[string]int64{
			"mock_heavy": 5,
		},
	}

	gov := mcpgov.NewMCPGovernor(v.name, callMap, opts)

	var server *plangate.MCPDPServer
	if v.disableBudgetLock {
		server = plangate.NewMCPDPServerNoLock(v.name, gov, 60*time.Second, v.maxConcurrentSessions, v.sunkCostAlpha)
	} else {
		server = plangate.NewMCPDPServer(v.name, gov, 60*time.Second, v.maxConcurrentSessions, v.sunkCostAlpha)
	}
	cfg := v.adaptiveAdmissionCfg
	cfg.Enabled = v.adaptiveAdmission
	server.SetAdaptiveAdmissionConfig(cfg)
	server.EnableAdaptiveAdmission(v.adaptiveAdmission)
	server.SetDisableCapacityStep0(v.disableCapacityStep0)
	server.SetReActRecoveryEnabled(v.reactRecoveryEnabled)
	if err := server.SetCommitmentTokenConfig(v.commitmentTokenConfig); err != nil {
		log.Fatalf("[%s] commitment token config error: %v", v.name, err)
	}
	if err := server.SetAmendmentPolicy(v.amendmentPolicy); err != nil {
		log.Fatalf("[%s] plan amendment policy error: %v", v.name, err)
	}

	detector := &proxyOverloadDetector{
		gov:      gov,
		interval: 10 * time.Millisecond,
	}
	go detector.run()

	// 璁剧疆鎶樻墸鍑芥暟锛堟秷铻嶅疄楠屾敮鎸侊級
	if v.discountFunc != "" {
		server.SetDiscountFunc(plangate.DiscountFuncName(v.discountFunc))
		log.Printf("  [%s] 鎶樻墸鍑芥暟: %s", v.name, v.discountFunc)
	}

	// Set sunk-cost beta.
	beta := v.sunkCostBeta
	if beta == 0 {
		beta = 1.0
	}
	server.SetSunkCostBeta(beta)
	log.Printf("  [%s] sunk-cost alpha=%.2f beta=%.2f", v.name, v.sunkCostAlpha, beta)

	// Multi-gateway: node ID and shared state store
	if v.nodeID != "" {
		server.SetNodeID(v.nodeID)
		log.Printf("  [%s] node-id: %s", v.name, v.nodeID)
	}
	if v.stateStoreType == "redis" && v.redisAddr != "" {
		store := plangate.NewRedisSessionStateStore(v.redisAddr)
		server.SetSharedStateStore(store)
		log.Printf("  [%s] plangate-state-store: redis @ %s", v.name, v.redisAddr)
	}

	// PlanGate-R: apply recovery config (no-op when Enabled=false)
	if v.recoveryConfig.Enabled {
		checkpointStore, err := buildRecoveryCheckpointStore(v.recoveryConfig, v.redisAddr)
		if err != nil {
			log.Fatalf("[%s] recovery store init error: %v", v.name, err)
		}
		if err := server.EnableRecoveryForConfig(v.recoveryConfig, checkpointStore); err != nil {
			log.Fatalf("[%s] recovery config error: %v", v.name, err)
		}
		log.Printf("  [%s] PlanGate-R recovery enabled (TTL=%v, maxAttempts=%d, store=%s)",
			v.name, v.recoveryConfig.TTL, v.recoveryConfig.MaxAttempts, v.recoveryConfig.Store)
	}

	for _, tool := range tools {
		server.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, detector))
		log.Printf("  [%s] 娉ㄥ唽宸ュ叿: %s", v.name, tool.Name)
	}
	return server
}

func init() {
	log.SetFlags(log.Ldate | log.Ltime | log.Lmicroseconds)
	log.SetOutput(os.Stdout)
}

func setupRajomon(tools []mcpgov.MCPTool, backendURL string, priceStep int64) http.Handler {
	gw := baseline.NewRajomonGateway("rajomon-gateway", baseline.RajomonConfig{
		PriceStep: priceStep,
	})
	for _, tool := range tools {
		gw.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, nil))
		log.Printf("  [Rajomon] 娉ㄥ唽宸ュ叿: %s (priceStep=%d)", tool.Name, priceStep)
	}
	return gw
}

func setupRajomonSession(tools []mcpgov.MCPTool, backendURL string, priceStep int64) http.Handler {
	gw := baseline.NewRajomonSessionGateway("rajomon-session-gateway", baseline.RajomonSessionConfig{
		RajomonConfig: baseline.RajomonConfig{
			PriceStep: priceStep,
		},
	})
	for _, tool := range tools {
		gw.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, nil))
		log.Printf("  [Rajomon+SB] 娉ㄥ唽宸ュ叿: %s (priceStep=%d)", tool.Name, priceStep)
	}
	return gw
}

func setupDagor(tools []mcpgov.MCPTool, backendURL string, rttThresholdMs float64, priceStep int64) http.Handler {
	gw := baseline.NewDagorGateway("dagor-gateway", baseline.DagorConfig{
		RTTThresholdMs: rttThresholdMs,
		PriceStep:      priceStep,
	})
	for _, tool := range tools {
		gw.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, nil))
		log.Printf("  [DAGOR] 娉ㄥ唽宸ュ叿: %s (rttThreshold=%.0fms, priceStep=%d)", tool.Name, rttThresholdMs, priceStep)
	}
	return gw
}

func setupSBAC(tools []mcpgov.MCPTool, backendURL string, maxSessions int64) http.Handler {
	gw := baseline.NewSBACGateway("sbac-gateway", baseline.SBACConfig{
		MaxSessions: maxSessions,
	})
	for _, tool := range tools {
		gw.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, nil))
		log.Printf("  [SBAC] 娉ㄥ唽宸ュ叿: %s (maxSessions=%d)", tool.Name, maxSessions)
	}
	return gw
}

func setupPP(tools []mcpgov.MCPTool, backendURL string, maxSessions int64) http.Handler {
	gw := baseline.NewPPGateway("pp-gateway", baseline.PPConfig{
		MaxSessions: maxSessions,
	})
	for _, tool := range tools {
		gw.RegisterTool(tool, makeProxyHandler(backendURL, tool.Name, nil))
		log.Printf("  [PP] 娉ㄥ唽宸ュ叿: %s (maxSessions=%d)", tool.Name, maxSessions)
	}
	return gw
}

// setupMCPDPReal 鍒涘缓浣跨敤澶栭儴淇″彿娌荤悊锟?PlanGate 缃戝叧锛堢湡锟?LLM 妯″紡锟?// 涓夌淮淇″彿: 429 棰戠巼 + 寤惰繜 P95 EMA + RateLimit-Remaining EMA
func setupMCPDPReal(tools []mcpgov.MCPTool, backendURL string, v mcpdpVariant,
	rateLimitMax float64, latencyThresholdMs float64) http.Handler {

	callMap := make(map[string][]string)
	for _, tool := range tools {
		callMap[tool.Name] = []string{}
	}

	opts := map[string]interface{}{
		"initprice":             int64(0),
		"rateLimiting":          false,
		"loadShedding":          true,
		"pinpointQueuing":       false,
		"latencyThreshold":      50 * time.Millisecond,
		"priceStep":             v.priceStep,
		"priceStrategy":         "expdecay",
		"priceDecayStep":        int64(1),
		"priceSensitivity":      int64(10000),
		"maxToken":              int64(20),
		"smoothingWindow":       5,
		"integralThreshold":     0.5,
		"priceUpdateRate":       5 * time.Millisecond,
		"tokenUpdateRate":       100 * time.Millisecond,
		"tokenUpdateStep":       int64(1),
		"tokenRefillDist":       "fixed",
		"priceAggregation":      "maximal",
		"enableAdaptiveProfile": true,
		"regimeWindow":          100,
		"regimeVarianceLow":     1.0,
		"regimeVarianceHigh":    4.0,
		"regimeSpikeThreshold":  2.0,
		"profileSwitchCooldown": 500 * time.Millisecond,
			"burstyProfile": map[string]interface{}{
				"DetectorMaxConc": int64(200),
			},
			"periodicProfile": map[string]interface{}{
				"DetectorMaxConc": int64(200),
			},
			"steadyProfile": map[string]interface{}{
				"DetectorMaxConc": int64(200),
			},
			"toolWeights": map[string]int64{
			"deepseek_llm":    5, // heavy LLM tool
			"real_web_search": 2, // medium search tool
		},
	}

	gov := mcpgov.NewMCPGovernor(v.name, callMap, opts)

	// Create external signal tracker.
	signalTracker := plangate.NewExternalSignalTracker(rateLimitMax, latencyThresholdMs)

	server := plangate.NewMCPDPServerWithExternalSignals(
		v.name, gov, 60*time.Second,
		v.maxConcurrentSessions, v.sunkCostAlpha, signalTracker,
		v.sessionCapWait, float64(v.priceStep),
	)
	cfg := v.adaptiveAdmissionCfg
	cfg.Enabled = v.adaptiveAdmission
	server.SetAdaptiveAdmissionConfig(cfg)
	server.EnableAdaptiveAdmission(v.adaptiveAdmission)
	server.SetDisableCapacityStep0(v.disableCapacityStep0)
	server.SetReActRecoveryEnabled(v.reactRecoveryEnabled)
	if err := server.SetCommitmentTokenConfig(v.commitmentTokenConfig); err != nil {
		log.Fatalf("[%s] commitment token config error: %v", v.name, err)
	}
	if err := server.SetAmendmentPolicy(v.amendmentPolicy); err != nil {
		log.Fatalf("[%s] plan amendment policy error: %v", v.name, err)
	}

	// Proxy overload detector still drives ownPrice for sunk-cost pricing.
	detector := &proxyOverloadDetector{
		gov:      gov,
		interval: 10 * time.Millisecond,
	}
	go detector.run()

	// 璁剧疆鎶樻墸鍑芥暟锛堟秷铻嶅疄楠屾敮鎸侊級
	if v.discountFunc != "" {
		server.SetDiscountFunc(plangate.DiscountFuncName(v.discountFunc))
		log.Printf("  [%s] 鎶樻墸鍑芥暟: %s", v.name, v.discountFunc)
	}

	// 璁剧疆 sunk-cost beta
	beta := v.sunkCostBeta
	if beta == 0 {
		beta = 1.0
	}
	server.SetSunkCostBeta(beta)
	log.Printf("  [%s] sunk-cost alpha=%.2f beta=%.2f", v.name, v.sunkCostAlpha, beta)

	// PlanGate-R: apply recovery config (no-op when Enabled=false)
	if v.recoveryConfig.Enabled {
		checkpointStore, err := buildRecoveryCheckpointStore(v.recoveryConfig, v.redisAddr)
		if err != nil {
			log.Fatalf("[%s] recovery store init error: %v", v.name, err)
		}
		if err := server.EnableRecoveryForConfig(v.recoveryConfig, checkpointStore); err != nil {
			log.Fatalf("[%s] recovery config error: %v", v.name, err)
		}
		log.Printf("  [%s] PlanGate-R recovery enabled (TTL=%v, maxAttempts=%d, store=%s)",
			v.name, v.recoveryConfig.TTL, v.recoveryConfig.MaxAttempts, v.recoveryConfig.Store)
	}

	for _, tool := range tools {
		server.RegisterTool(tool, makeProxyHandlerWithSignals(
			backendURL, tool.Name, detector, signalTracker,
		))
		log.Printf("  [%s] 娉ㄥ唽宸ュ叿: %s (澶栭儴淇″彿娌荤悊)", v.name, tool.Name)
	}
	return server
}

// buildRecoveryConfig converts CLI flags into plangate.RecoveryConfig.
func buildRecoveryConfig(enabled bool, ttl time.Duration, maxAttempts int, store string) plangate.RecoveryConfig {
	if !enabled {
		return plangate.DefaultRecoveryConfig()
	}
	return plangate.RecoveryConfig{
		Enabled:     true,
		TTL:         ttl,
		MaxAttempts: maxAttempts,
		Store:       store,
	}
}

func buildRecoveryCheckpointStore(cfg plangate.RecoveryConfig, redisAddr string) (plangate.CheckpointStore, error) {
	if !cfg.Enabled {
		return nil, nil
	}
	switch cfg.Store {
	case "inmemory":
		return nil, nil
	case "redis":
		if redisAddr == "" {
			return nil, fmt.Errorf("redis checkpoint store requires a redis address")
		}
		store := plangate.NewRedisCheckpointStore(redisAddr)
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		if err := store.Ping(ctx); err != nil {
			return nil, fmt.Errorf("redis checkpoint store unavailable at %s: %w", redisAddr, err)
		}
		return store, nil
	default:
		return nil, fmt.Errorf("unsupported recovery store %q", cfg.Store)
	}
}

// makeProxyHandlerWithSignals 鍒涘缓淇″彿鎰熺煡鐨勪唬鐞嗗鐞嗗嚱锟?// 鍦ㄦ爣鍑嗕唬鐞嗗熀纭€涓婏紝瑙ｆ瀽鍚庣 _meta 涓殑澶栭儴 API 淇″彿骞舵姤鍛婄粰 ExternalSignalTracker
func makeProxyHandlerWithSignals(
	backendURL string, toolName string,
	detector *proxyOverloadDetector,
	signalTracker *plangate.ExternalSignalTracker,
) mcpgov.ToolCallHandler {
	proxyTransport := &http.Transport{
		MaxIdleConns:        256,
		MaxIdleConnsPerHost: 128,
		MaxConnsPerHost:     0,
		IdleConnTimeout:     90 * time.Second,
	}
	client := &http.Client{Timeout: 120 * time.Second, Transport: proxyTransport}

	return func(ctx context.Context, params mcpgov.MCPToolCallParams) (*mcpgov.MCPToolCallResult, error) {
		if detector != nil {
			detector.onRequestStart()
			defer detector.onRequestEnd()
		}

		start := time.Now()

		// 鏋勫缓鍙戝線鍚庣锟?JSON-RPC 璇锋眰
		rpcReq := mcpgov.JSONRPCRequest{
			JSONRPC: "2.0",
			ID:      fmt.Sprintf("proxy-%s-%d", toolName, time.Now().UnixNano()),
			Method:  "tools/call",
		}
		paramsBytes, _ := json.Marshal(map[string]interface{}{
			"name":      params.Name,
			"arguments": params.Arguments,
		})
		rpcReq.Params = paramsBytes

		body, _ := json.Marshal(rpcReq)

		req, err := http.NewRequestWithContext(ctx, http.MethodPost, backendURL, bytes.NewReader(body))
		if err != nil {
			return nil, fmt.Errorf("鍒涘缓鍚庣璇锋眰澶辫触: %w", err)
		}
		req.Header.Set("Content-Type", "application/json")

		resp, err := client.Do(req)
		if err != nil {
			// 缃戠粶閿欒 锟?鎶ュ憡涓洪珮寤惰繜淇″彿
			elapsed := time.Since(start).Seconds() * 1000
			if signalTracker != nil {
				signalTracker.ReportResponse(false, elapsed, -1)
			}
			return nil, fmt.Errorf("鍚庣璋冪敤澶辫触: %w", err)
		}
		defer resp.Body.Close()

		data, _ := io.ReadAll(resp.Body)
		elapsed := time.Since(start).Seconds() * 1000

		var rpcResp struct {
			Result *struct {
				Content []mcpgov.ContentBlock `json:"content"`
				Meta    *struct {
					Tool               string  `json:"tool"`
					Category           string  `json:"category"`
					LatencyMs          float64 `json:"latency_ms"`
					Is429              bool    `json:"is_429"`
					HttpStatus         int     `json:"http_status"`
					ApiLatencyMs       float64 `json:"api_latency_ms"`
					RateLimitRemaining float64 `json:"rate_limit_remaining"`
				} `json:"_meta"`
			} `json:"result"`
			Error *mcpgov.RPCError `json:"error"`
		}
		if err := json.Unmarshal(data, &rpcResp); err != nil {
			if signalTracker != nil {
				signalTracker.ReportResponse(false, elapsed, -1)
			}
			return nil, fmt.Errorf("瑙ｆ瀽鍚庣鍝嶅簲澶辫触: %w", err)
		}

		// 鎶ュ憡澶栭儴 API 淇″彿缁欒窡韪櫒
		if signalTracker != nil && rpcResp.Result != nil && rpcResp.Result.Meta != nil {
			meta := rpcResp.Result.Meta
			apiLatency := meta.ApiLatencyMs
			if apiLatency <= 0 {
				apiLatency = elapsed // 鍥為€€鍒扮鍒扮寤惰繜
			}
			signalTracker.ReportResponse(meta.Is429, apiLatency, meta.RateLimitRemaining)
		} else if signalTracker != nil {
			// Without backend meta, use end-to-end latency as fallback.
			signalTracker.ReportResponse(false, elapsed, -1)
		}

		if rpcResp.Error != nil {
			// 鍚庣杩斿洖閿欒 锟?妫€鏌ユ槸鍚︿负杩囪浇 (429/503)
			is429 := resp.StatusCode == 429
			if signalTracker != nil && is429 {
				signalTracker.ReportResponse(true, elapsed, -1)
			}
			return nil, fmt.Errorf("鍚庣宸ュ叿鎵ц閿欒: %s", rpcResp.Error.Message)
		}
		if rpcResp.Result == nil {
			return nil, fmt.Errorf("backend returned empty result")
		}

		return &mcpgov.MCPToolCallResult{
			Content: rpcResp.Result.Content,
		}, nil
	}
}
