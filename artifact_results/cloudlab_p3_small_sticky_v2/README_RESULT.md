## CloudLab P3 Small Sticky Stress

- Date: `2026-05-23`
- Profile: `small`
- Topology: `1 loader, 1 Redis, 2 gateways, 2 backends`
- Routing: `sticky`
- Sessions: `100`
- Concurrency: `10`
- Failure rates: `0.1, 0.2, 0.3`
- Amendment rate: `1.0`
- Policies: `naive_retry, plangate_r, plangate_ar`
- Validation mode: `stress`

Validated files in this directory:

- `p3_summary.csv`
- `p3_adversarial_summary.csv`
- `summary.csv`
- `validation.json`

Key results:

- `plangate_ar success_rate = 1.0 / 1.0 / 1.0`
- `plangate_ar recovery_success_rate = 1.0 / 1.0 / 1.0`
- `plangate_ar amendment_accept_rate = 1.0 / 1.0 / 1.0`
- `v2_commitment_issued = 10 / 20 / 30`
- `plangate_ar avg_total_tool_calls = 5.1 / 5.2 / 5.3`
- `naive_retry avg_total_tool_calls = 5.3 / 5.6 / 5.9`
- invalid amendment `reject_rate = 1.0` for all 6 adversarial cases
- `commitment_invalid = 0`
- `commitment_mismatch = 0`
- `commitment_expired = 0`
- `state_miss = 0`
- `duplicate_admission = 0`
- `validation.json.errors = []`

Limitation:

This result uses sticky per-session routing because the current recovery
checkpoint store is gateway-local/in-memory. It validates CloudLab multi-node
execution with gateway-local recovery affinity, not random cross-gateway
recovery. Random cross-gateway recovery requires Redis/shared checkpoint store
or checkpoint-owner routing.
