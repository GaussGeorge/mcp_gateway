#!/usr/bin/env python3

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
RUNNER_PATH = SCRIPT_DIR / "run_p3_failure_mechanism_ablation.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = load_module(RUNNER_PATH, "run_p3_failure_mechanism_ablation")


class P3FailureMechanismAblationRunnerTests(unittest.TestCase):
    def test_variants_cover_expected_matrix(self):
        variants = runner.mechanism_variants()
        self.assertEqual(
            ["plangate_full", "wo_commitment", "wo_amendment", "wo_recovery"],
            [variant.name for variant in variants],
        )

    def test_variants_are_single_variable_ablations(self):
        variants = {variant.name: variant for variant in runner.mechanism_variants()}
        full = variants["plangate_full"]
        wo_commitment = variants["wo_commitment"]
        wo_amendment = variants["wo_amendment"]
        wo_recovery = variants["wo_recovery"]

        self.assertNotEqual(full.commitment_token_mode, wo_commitment.commitment_token_mode)
        self.assertEqual(full.plan_amendment_mode, wo_commitment.plan_amendment_mode)
        self.assertEqual(full.enable_recovery, wo_commitment.enable_recovery)

        self.assertEqual(full.commitment_token_mode, wo_amendment.commitment_token_mode)
        self.assertNotEqual(full.plan_amendment_mode, wo_amendment.plan_amendment_mode)
        self.assertEqual(full.enable_recovery, wo_amendment.enable_recovery)

        self.assertEqual(full.commitment_token_mode, wo_recovery.commitment_token_mode)
        self.assertEqual(full.plan_amendment_mode, wo_recovery.plan_amendment_mode)
        self.assertNotEqual(full.enable_recovery, wo_recovery.enable_recovery)

    def test_recovery_store_normalization_preserves_aliases(self):
        self.assertEqual("inmemory", runner.normalize_recovery_store("memory"))
        self.assertEqual("inmemory", runner.normalize_recovery_store("inmemory"))
        self.assertEqual("redis", runner.normalize_recovery_store("redis"))

    def test_summary_path_and_results_dir_are_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = runner.parse_args(["--results-dir", tmp, "--dry-run"])
            self.assertEqual(
                Path(tmp) / "p3_failure_mechanism_ablation_summary.csv",
                runner.summary_path(Path(args.results_dir)),
            )
            self.assertEqual(
                Path(tmp) / "plangate_full" / "run1",
                runner.run_dir_for_variant(Path(args.results_dir), "plangate_full", 1),
            )

    def test_dry_run_prints_variants_without_starting_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with mock.patch.object(runner, "run_variant_once", side_effect=AssertionError("should not run")), \
                 mock.patch.object(runner.p3_runner, "build_gateway", side_effect=AssertionError("should not build")), \
                 contextlib.redirect_stdout(out):
                rc = runner.main(
                    [
                        "--repeats",
                        "1",
                        "--sessions",
                        "200",
                        "--concurrency",
                        "50",
                        "--failure-rate",
                        "0.2",
                        "--amendment-rate",
                        "0.2",
                        "--results-dir",
                        tmp,
                        "--gateway-binary",
                        "gateway.exe",
                        "--dry-run",
                    ]
                )
            self.assertEqual(0, rc)
            text = out.getvalue()
            self.assertIn("variant = plangate_full", text)
            self.assertIn("variant = wo_commitment", text)
            self.assertIn("variant = wo_amendment", text)
            self.assertIn("variant = wo_recovery", text)
            self.assertIn("commitment-token-mode = optional", text)
            self.assertIn("commitment-token-mode = off", text)
            self.assertIn("plan-amendment-mode = off", text)
            self.assertIn("enable-recovery = false", text)

    def test_gateway_command_contains_expected_switches(self):
        args = runner.parse_args(["--gateway-binary", "gateway.exe", "--recovery-store", "memory", "--dry-run"])
        variant = runner.mechanism_variants(args.recovery_store)[1]  # wo_commitment
        cmd = runner.gateway_command_for_variant(Path("gateway.exe"), args, variant)
        joined = " ".join(cmd)
        self.assertIn("--commitment-token-mode off", joined)
        self.assertIn("--plan-amendment-mode recovery-only", joined)
        self.assertIn("--enable-recovery=true", joined)
        self.assertIn("--recovery-store inmemory", joined)

    def test_p3_runner_command_uses_external_gateway_mode(self):
        args = runner.parse_args(["--results-dir", "tmp-results", "--dry-run"])
        cmd = runner.p3_runner_command(args, Path("tmp-results"), "http://127.0.0.1:9701")
        joined = " ".join(cmd)
        self.assertIn("--policies plangate_ar", joined)
        self.assertIn("--no-start-services", joined)
        self.assertIn("--gateway-url http://127.0.0.1:9701", joined)
        self.assertIn("--adversarial-amendment-rate 0", joined)


if __name__ == "__main__":
    unittest.main()
