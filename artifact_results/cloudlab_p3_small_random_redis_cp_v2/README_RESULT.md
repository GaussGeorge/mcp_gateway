## CloudLab P4 Random Redis Checkpoint Result

- Profile: `small`
- Topology: `1 loader, 1 Redis, 2 gateways, 2 backends`
- Routing: `random`
- Recovery store: `redis`
- State store: `redis`
- Policies: `naive_retry, plangate_r, plangate_ar`
- Sessions: `100` per failure rate
- Concurrency: `10`
- Failure rates: `0.1, 0.2, 0.3`
- Amendment rate: `1.0`
- Validation result: `errors = []`

Validated files in this directory:

- `p3_summary.csv`
- `p3_adversarial_summary.csv`
- `summary.csv`
- `validation.json`

Core distributed evidence:

- `cross_node_sessions = 843`
- `state_miss = 0`
- `duplicate_admission = 0`
- `commitment_invalid = 0`
- `commitment_mismatch = 0`
- `commitment_expired = 0`

Primary `plangate_ar` results:

- `plangate_ar success_rate = 1.0 / 1.0 / 1.0`
- `plangate_ar amendment_accept_rate = 1.0 / 1.0 / 1.0`
- `plangate_ar v2_commitment_issued = 10 / 20 / 30`
- `plangate_ar avg_total_tool_calls = 5.1 / 5.2 / 5.3`

Naive retry control:

- `naive_retry avg_total_tool_calls = 5.3 / 5.6 / 5.9`

Adversarial amendment results:

- all adversarial amendment `reject_rate = 1.0`
- `false_accept = 0`
- `executed_after_rejected_amendment = 0`

This evidence supersedes the earlier sticky-only CloudLab evidence for the
specific claim of random cross-gateway recovery. The sticky result remains
useful as a simpler baseline, while this random+Redis result is the stronger
distributed recovery evidence.
