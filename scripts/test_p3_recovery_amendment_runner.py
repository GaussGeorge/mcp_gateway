#!/usr/bin/env python3

import asyncio
import contextlib
import csv
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
RUNNER_PATH = SCRIPT_DIR / "p3_recovery_amendment_runner.py"
STATS_PATH = SCRIPT_DIR / "compute_p3_recovery_amendment_stats.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = load_module(RUNNER_PATH, "p3_recovery_amendment_runner")
stats = load_module(STATS_PATH, "compute_p3_recovery_amendment_stats")


class P3RunnerUnitTests(unittest.TestCase):
    def setUp(self):
        self.parent_claims = {
            "v": 1,
            "typ": "ps_commitment",
            "sid": "sess-1",
            "plan_hash": "base-plan-hash",
            "price_hash": "price-hash",
            "budget": 900,
            "total_cost": 200,
            "total_steps": 5,
            "iat": 1710000000,
            "exp": 2710000000,
            "policy_version": "plangate-v1",
            "node_id": "gw-a",
            "state_store": "local",
            "recovery_enabled": True,
        }
        self.secret = "unit-test-secret"
        self.parent_token = runner.sign_commitment_token(self.secret, self.parent_claims)

    def test_build_legal_amendment_uses_parent_hashes(self):
        amendment = runner.build_legal_amendment(
            "sess-1",
            self.parent_claims,
            self.parent_token,
            runner.DEFAULT_FAIL_STEP_INDEX,
        )
        self.assertEqual("sess-1", amendment["session_id"])
        self.assertEqual("base-plan-hash", amendment["base_plan_hash"])
        self.assertEqual(
            runner.commitment_token_hash(self.parent_token),
            amendment["parent_commitment_digest"],
        )
        self.assertEqual(3, len(amendment["replacement_suffix"]))
        self.assertEqual("s2", amendment["replacement_suffix"][0]["depends_on"][0])

    def test_invalid_amendment_cases_cover_expected_shapes(self):
        expected = {
            "modify_completed_prefix": lambda amend, token: amend["replacement_suffix"][0]["step_id"] == "s1" and token == self.parent_token,
            "unknown_tool": lambda amend, token: amend["replacement_suffix"][0]["tool_name"] == "ghost_tool" and token == self.parent_token,
            "budget_overflow": lambda amend, token: len(amend["replacement_suffix"]) == 5 and token == self.parent_token,
            "dag_cycle": lambda amend, token: amend["replacement_suffix"][0]["depends_on"] == ["s4_retry"] and token == self.parent_token,
            "stale_parent": lambda amend, token: runner.decode_commitment_token_unverified(token)["v"] == 2,
            "checkpoint_hash_mismatch": lambda amend, token: runner.decode_commitment_token_unverified(token)["v"] == 2,
        }
        for kind, checker in expected.items():
            with self.subTest(kind=kind):
                amendment, token = runner.build_invalid_amendment_case(
                    kind,
                    self.secret,
                    "sess-1",
                    self.parent_token,
                    self.parent_claims,
                    runner.DEFAULT_FAIL_STEP_INDEX,
                    900,
                )
                self.assertTrue(checker(amendment, token))

    def test_synthetic_v2_parent_binds_checkpoint_hash(self):
        token = runner.build_synthetic_v2_parent(
            self.secret,
            self.parent_claims,
            self.parent_token,
            "checkpoint-hash-x",
            "amend-x",
        )
        claims = runner.decode_commitment_token_unverified(token)
        self.assertEqual(2, claims["v"])
        self.assertEqual("ps_amended_commitment", claims["typ"])
        self.assertEqual("checkpoint-hash-x", claims["checkpoint_hash"])
        self.assertEqual(
            runner.commitment_token_hash(self.parent_token),
            claims["parent_commitment_hash"],
        )

    def test_retryable_recovery_errors_are_classified(self):
        retryable = {
            "error": {
                "message": "session is still active (ACTIVE_CHECKPOINT); cannot resume a live session",
            }
        }
        non_retryable = {
            "error": {
                "message": "amendment rejected",
                "data": {"reason": "unknown tool"},
            }
        }
        self.assertTrue(runner.is_retryable_recovery_response(retryable))
        self.assertFalse(runner.is_retryable_recovery_response(non_retryable))

    def test_dry_run_external_gateway_mode_does_not_require_aiohttp(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = runner.parse_args(
                [
                    "--commitment-secret",
                    "unit-secret",
                    "--gateway-urls",
                    "http://gw-a:9601",
                    "http://gw-b:9602",
                    "--routing",
                    "random",
                    "--no-start-services",
                    "--dry-run",
                    "--results-dir",
                    tmp,
                ]
            )
            previous = runner.aiohttp
            runner.aiohttp = None
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    rc = asyncio.run(runner.async_main(args))
            finally:
                runner.aiohttp = previous

            self.assertEqual(0, rc)
            plan = json.loads(out.getvalue())
            self.assertEqual(
                ["http://gw-a:9601", "http://gw-b:9602"],
                plan["gateway_urls"],
            )
            self.assertEqual("random", plan["routing"])
            self.assertFalse(plan["start_services"])

    def test_compute_stats_splits_main_and_adversarial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy_dir = root / "plangate_ar"
            policy_dir.mkdir(parents=True, exist_ok=True)
            rows = [
                {
                    "session_id": "main-1",
                    "policy": "plangate_ar",
                    "scenario": "main",
                    "failure_rate": "0.1",
                    "recovery_attempted": "1",
                    "recovery_success": "1",
                    "amendment_submitted": "1",
                    "amendment_accepted": "1",
                    "amendment_rejected": "0",
                    "v2_commitment_issued": "1",
                    "false_accept": "0",
                    "executed_after_rejected_amendment": "0",
                    "total_tool_calls": "6",
                    "saved_steps": "2",
                    "latency_ms": "123.0",
                    "status": "success",
                    "invalid_amendment_rejected": "0",
                    "stale_parent_rejected": "0",
                },
                {
                    "session_id": "invalid-1",
                    "policy": "plangate_ar",
                    "scenario": "unknown_tool",
                    "failure_rate": "0.1",
                    "recovery_attempted": "1",
                    "recovery_success": "0",
                    "amendment_submitted": "1",
                    "amendment_accepted": "0",
                    "amendment_rejected": "1",
                    "v2_commitment_issued": "0",
                    "false_accept": "0",
                    "executed_after_rejected_amendment": "0",
                    "total_tool_calls": "3",
                    "saved_steps": "0",
                    "latency_ms": "45.0",
                    "status": "amendment_rejected",
                    "invalid_amendment_rejected": "1",
                    "stale_parent_rejected": "0",
                },
            ]
            with (policy_dir / "sessions.csv").open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                for row in rows:
                    writer.writerow(row)

            collected = stats.collect_policy_rows(root)
            main_summary = stats.summarize_main(collected)
            adv_summary = stats.summarize_adversarial(collected)

            self.assertEqual(1, len(main_summary))
            self.assertEqual("plangate_ar", main_summary[0]["policy"])
            self.assertEqual(1, main_summary[0]["v2_commitment_issued"])
            self.assertEqual(1.0, main_summary[0]["success_rate"])
            self.assertEqual(1.0, main_summary[0]["amendment_accept_rate"])

            self.assertEqual(1, len(adv_summary))
            self.assertEqual("unknown_tool", adv_summary[0]["scenario"])
            self.assertEqual(1.0, adv_summary[0]["reject_rate"])
            self.assertEqual(0, adv_summary[0]["false_accept"])


if __name__ == "__main__":
    unittest.main()
