package baseline

import "context"

// ReqDesc captures only request-local information visible to proxy-style baselines.
// It intentionally excludes full DAG semantics and any future-step reservation state.
type ReqDesc struct {
	SessionID   string
	ConsumerKey string
	Route       string
	Method      string
	RemoteAddr  string
	Tokens      int64
	Headers     map[string]string
}

// Decision is the pre-admission result from a proxy approximation hook.
type Decision struct {
	Allow  bool
	Reason string
}

// HookState is implementation-private state returned by Before and consumed by After.
type HookState interface{}

// ProxyHook defines request-level governance primitives shared by Envoy/Kong approximations.
type ProxyHook interface {
	Before(ctx context.Context, req ReqDesc) (Decision, HookState)
	After(ctx context.Context, req ReqDesc, state HookState, callErr error)
}
