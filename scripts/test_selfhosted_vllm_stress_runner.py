#!/usr/bin/env python3

import contextlib
import csv
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).resolve().parent
RUNNER_PATH = SCRIPT_DIR / "run_selfhosted_vllm_stress.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = load_module(RUNNER_PATH, "run_selfhosted_vllm_stress")


class SelfHostedVLLMStressRunnerTests(unittest.TestCase):
    def test_registry_contains_required_gateways_including_relaxed(self):
        required = {"ng", "static", "pp", "rajomon", "plangate_real", "plangate_relaxed"}
        self.assertTrue(required.issubset(set(runner.GATEWAY_REGISTRY.keys())))

    def test_plangate_relaxed_registry_entry_matches_expected_mode_and_args(self):
        cfg = runner.GATEWAY_REGISTRY["plangate_relaxed"]
        self.assertEqual("mcpdp-real", cfg.mode)
        self.assertIn("--plangate-max-sessions", cfg.extra_args)
        self.assertIn("24", cfg.extra_args)
        self.assertIn("--plangate-session-cap-wait", cfg.extra_args)
        self.assertIn("6", cfg.extra_args)
        self.assertIn("PlanGate relaxed admission baseline", cfg.label)

    def test_static_alias_resolves_to_static_baseline(self):
        cfgs = runner.select_gateway_configs(["static"])
        self.assertEqual(1, len(cfgs))
        self.assertEqual("static", cfgs[0].name)
        self.assertEqual("srl", cfgs[0].mode)
        self.assertIn("static rate limit baseline", cfgs[0].label)

    def test_unknown_gateway_error_lists_supported_gateways(self):
        with self.assertRaises(SystemExit) as ctx:
            runner.select_gateway_configs(["unknown_gateway"])
        msg = str(ctx.exception)
        self.assertIn("unknown gateways requested", msg)
        self.assertIn("supported gateways", msg)
        self.assertIn("static", msg)
        self.assertIn("plangate_real", msg)
        self.assertIn("plangate_relaxed", msg)

    def test_expected_summary_rows_scale_with_gateway_count(self):
        args = runner.parse_args(
            ["--repeats", "3", "--gateways", "ng", "static", "pp", "rajomon", "plangate_real", "plangate_relaxed"]
        )
        settings = runner.resolve_settings(args)
        gateway_cfgs = runner.select_gateway_configs(settings["gateways"])
        expected_rows = args.repeats * len(gateway_cfgs)
        self.assertEqual(18, expected_rows)

    def test_profile_defaults_and_overrides_flow_through(self):
        args = runner.parse_args(["--stress-profile", "stress", "--concurrency", "5", "--max-workers", "4"])
        settings = runner.resolve_settings(args)
        self.assertEqual(100, settings["agents"])
        self.assertEqual(5, settings["concurrency"])
        self.assertEqual(4, settings["max_workers"])
        self.assertEqual(25, settings["burst_size"])
        self.assertEqual(("ng", "plangate_real"), settings["gateways"])

    def test_results_paths_are_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = runner.parse_args(["--out-dir", tmp, "--dry-run"])
            settings = runner.resolve_settings(args)
            root = runner.results_dir(args, settings)
            self.assertEqual(Path(tmp), root)
            self.assertEqual(Path(tmp) / "selfhosted_vllm_stress_summary.csv", runner.summary_path(root))
            self.assertEqual(
                Path(tmp) / "plangate_real" / "run2",
                runner.run_dir_for(root, "plangate_real", 2),
            )

    def test_dry_run_does_not_start_long_experiment(self):
        out = io.StringIO()
        with mock.patch.object(runner, "run_experiment", side_effect=AssertionError("should not run")), \
             contextlib.redirect_stdout(out):
            rc = runner.main(
                [
                    "--repeats",
                    "1",
                    "--agents",
                    "20",
                    "--concurrency",
                    "5",
                    "--max-workers",
                    "4",
                    "--burst-size",
                    "10",
                    "--burst-gap",
                    "2",
                    "--max-steps",
                    "8",
                    "--gateways",
                    "ng",
                    "plangate_real",
                    "--out-dir",
                    "results/exp_selfhosted_vllm_stress_dryrun",
                    "--dry-run",
                ]
            )
        self.assertEqual(0, rc)
        text = out.getvalue()
        self.assertIn("agents=20", text)
        self.assertIn("concurrency=5", text)
        self.assertIn("max_workers=4", text)
        self.assertIn("burst_size=10", text)
        self.assertIn("burst_gap=2.0", text)
        self.assertIn("max_steps=8", text)
        self.assertIn("gateways=ng, plangate_real", text)
        self.assertIn("vllm_base=http://127.0.0.1:9999/v1", text)
        self.assertIn("vllm_model=qwen", text)
        self.assertIn("backend_cmd:", text)
        self.assertIn("gateway=ng", text)
        self.assertIn("gateway=plangate_real", text)
        self.assertIn("mode=ng", text)
        self.assertIn("mode=mcpdp-real", text)
        self.assertIn("extra_args=[", text)
        self.assertIn("gateway_cmd:", text)
        self.assertIn("client_cmd:", text)

    def test_dry_run_supports_relaxed_gateway_baseline(self):
        out = io.StringIO()
        with mock.patch.object(runner, "run_experiment", side_effect=AssertionError("should not run")), \
             contextlib.redirect_stdout(out):
            rc = runner.main(
                [
                    "--repeats",
                    "1",
                    "--stress-profile",
                    "c40w8",
                    "--task-profile",
                    "stress",
                    "--gateways",
                    "ng",
                    "static",
                    "pp",
                    "rajomon",
                    "plangate_real",
                    "plangate_relaxed",
                    "--gateway-binary",
                    "gateway.exe",
                    "--client-timeout",
                    "2400",
                    "--out-dir",
                    "results/exp_selfhosted_vllm_stress_c40w8_5gw",
                    "--dry-run",
                ]
            )
        self.assertEqual(0, rc)
        text = out.getvalue()
        self.assertIn("gateway=ng", text)
        self.assertIn("gateway=static", text)
        self.assertIn("gateway=pp", text)
        self.assertIn("gateway=rajomon", text)
        self.assertIn("gateway=plangate_real", text)
        self.assertIn("gateway=plangate_relaxed", text)
        self.assertIn("label=static rate limit baseline", text)
        self.assertIn("label=PlanGate relaxed admission baseline", text)
        self.assertIn("mode=mcpdp-real", text)
        self.assertIn("--plangate-max-sessions 24", text)
        self.assertIn("--plangate-session-cap-wait 6", text)
        self.assertIn("--plangate-price-step 30", text)

    def test_client_command_carries_stress_settings(self):
        args = runner.parse_args(
            [
                "--agents",
                "20",
                "--concurrency",
                "5",
                "--max-workers",
                "4",
                "--burst-size",
                "10",
                "--burst-gap",
                "2",
                "--max-steps",
                "8",
                "--budget",
                "700",
            ]
        )
        settings = runner.resolve_settings(args)
        cmd = runner.client_command(
            settings,
            "http://127.0.0.1:9500",
            Path("results/out.csv"),
            "ng",
        )
        joined = " ".join(cmd)
        self.assertIn("--agents 20", joined)
        self.assertIn("--concurrency 5", joined)
        self.assertIn("--max-steps 8", joined)
        self.assertIn("--budget 700", joined)
        self.assertIn("--burst-size 10", joined)
        self.assertIn("--burst-gap 2.0", joined)
        self.assertIn("--task-profile stress", joined)

    def test_validate_summary_detects_missing_and_error_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.csv"
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=runner.SUMMARY_FIELDS)
                writer.writeheader()
                writer.writerow(
                    {
                        "gateway": "ng",
                        "run": "1",
                        "success": "18",
                        "partial": "1",
                        "all_rejected": "1",
                        "error": "0",
                        "abd_total": "5.0",
                        "success_rate": "90.0",
                        "cascade_agents": "2",
                        "cascade_steps": "4",
                        "eff_gps": "0.55",
                        "p50_ms": "2000",
                        "p95_ms": "9000",
                        "http_429_count": "3",
                        "client_rc": "0",
                        "client_timed_out": "0",
                        "csv": "steps.csv",
                    }
                )
                writer.writerow(
                    {
                        "gateway": "",
                        "run": "2",
                        "success": "bad",
                        "partial": "0",
                        "all_rejected": "0",
                        "error": "boom",
                        "abd_total": "0",
                        "success_rate": "0",
                        "cascade_agents": "0",
                        "cascade_steps": "0",
                        "eff_gps": "0",
                        "p50_ms": "0",
                        "p95_ms": "0",
                        "http_429_count": "0",
                        "client_rc": "1",
                        "client_timed_out": "1",
                        "csv": "steps.csv",
                    }
                )
            validation = runner.validate_summary(path)
            self.assertEqual(2, validation["row_count"])
            self.assertFalse(validation["all_client_rc_zero"])
            self.assertFalse(validation["all_client_timed_out_zero"])
            self.assertFalse(validation["all_error_empty"])
            self.assertTrue(validation["errors"])


if __name__ == "__main__":
    unittest.main()
