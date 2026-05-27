#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
RUNNER_PATH = SCRIPT_DIR / "run_cloudlab_experiment.py"
WRAPPER_PATH = SCRIPT_DIR / "run_random_state_store_comparison.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = load_module(RUNNER_PATH, "run_cloudlab_experiment_testable")
wrapper = load_module(WRAPPER_PATH, "run_random_state_store_comparison_testable")


class RandomStateStoreComparisonTests(unittest.TestCase):
    def test_inventory_m510_6_shape(self):
        inventory_path = SCRIPT_DIR / "inventory.m510_6.json"
        data = json.loads(inventory_path.read_text(encoding="utf-8"))
        self.assertEqual("node-0", data["redis"])
        self.assertEqual(["node-1"], data["loaders"])
        self.assertEqual(["node-2", "node-3"], data["gateways"])
        self.assertEqual(["node-4", "node-5"], data["backends"])

    def test_memory_normalizes_to_inmemory(self):
        self.assertEqual("inmemory", runner.normalize_store_name("memory"))
        self.assertEqual("inmemory", runner.normalize_store_name("inmemory"))
        self.assertEqual("redis", runner.normalize_store_name("redis"))

    def test_wrapper_dry_run_does_not_create_results_dir_and_shows_both_modes(self):
        with tempfile.TemporaryDirectory() as tmp_root, tempfile.TemporaryDirectory() as tmp_artifact:
            out_dir = Path(tmp_root) / "cloudlab_random_redis_memory"
            artifact_dir = Path(tmp_artifact) / "artifact"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = wrapper.main(
                    [
                        "--inventory",
                        str(SCRIPT_DIR / "inventory.m510_6.json"),
                        "--sessions",
                        "1000",
                        "--concurrency",
                        "100",
                        "--repeats",
                        "3",
                        "--failure-rate",
                        "0.1",
                        "0.2",
                        "0.3",
                        "--amendment-rate",
                        "0.2",
                        "--results-dir",
                        str(out_dir),
                        "--artifact-dir",
                        str(artifact_dir),
                        "--dry-run",
                    ]
                )
            self.assertEqual(0, rc)
            text = out.getvalue()
            self.assertIn("--plangate-state-store redis", text)
            self.assertIn("--recovery-store redis", text)
            self.assertIn("--plangate-state-store inmemory", text)
            self.assertIn("--recovery-store inmemory", text)
            self.assertIn("--routing random", text)
            self.assertIn("--failure-rate 0.1 0.2 0.3", text)
            self.assertIn("--amendment-rate 0.2", text)
            self.assertNotIn("node-6", text)
            self.assertNotIn("node-7", text)
            self.assertFalse(out_dir.exists())

    def test_direct_runner_dry_run_prints_both_state_store_fields(self):
        inventory = runner.load_inventory(str(SCRIPT_DIR / "inventory.m510_6.json"))
        topology = runner.build_topology(inventory, "small")
        with tempfile.TemporaryDirectory() as tmp_root:
            out_dir = Path(tmp_root) / "redis_run"
            argv = [
                "run_cloudlab_experiment.py",
                "--inventory",
                str(SCRIPT_DIR / "inventory.m510_6.json"),
                "--profile",
                "small",
                "--workload",
                "p3",
                "--routing",
                "random",
                "--plangate-state-store",
                "redis",
                "--recovery-store",
                "redis",
                "--failure-rate",
                "0.1",
                "0.2",
                "0.3",
                "--amendment-rate",
                "0.2",
                "--repeats",
                "3",
                "--sessions",
                "1000",
                "--concurrency",
                "100",
                "--validation-mode",
                "correctness",
                "--results-dir",
                str(out_dir),
                "--dry-run",
            ]
            old_argv = sys.argv[:]
            out = io.StringIO()
            try:
                sys.argv = argv
                with contextlib.redirect_stdout(out):
                    rc = runner.main()
            finally:
                sys.argv = old_argv
            self.assertEqual(0, rc)
            text = out.getvalue()
            self.assertIn('"plangate_state_store": "redis"', text)
            self.assertIn('"recovery_store": "redis"', text)
            self.assertIn("--plangate-state-store redis", text)
            self.assertIn("--recovery-store redis", text)
            self.assertNotIn("node-6", text)
            self.assertNotIn("node-7", text)
            self.assertFalse(out_dir.exists())

    def test_memory_mode_command_uses_inmemory_and_stress(self):
        args = wrapper.parse_args(
            [
                "--inventory",
                str(SCRIPT_DIR / "inventory.m510_6.json"),
                "--results-dir",
                "results/cloudlab_random_redis_memory",
            ]
        )
        cmd = wrapper.build_mode_command(args, mode="memory")
        joined = " ".join(cmd)
        self.assertIn("--plangate-state-store inmemory", joined)
        self.assertIn("--recovery-store inmemory", joined)
        self.assertIn("--validation-mode stress", joined)
        self.assertIn("results/cloudlab_random_redis_memory", joined)
        self.assertIn("memory", joined)


if __name__ == "__main__":
    unittest.main()
