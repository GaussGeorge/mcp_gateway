#!/usr/bin/env python3

import importlib.util
import os
import tempfile
import unittest


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.join(SCRIPT_DIR, "run_multigateway_shared_state.py")

spec = importlib.util.spec_from_file_location("run_multigateway_shared_state", SCRIPT_PATH)
mgw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mgw)


class FakeProc:
    def __init__(self, cmd):
        self.cmd = cmd
        self.pid = 4242

    def poll(self):
        return None


def arg_value(cmd, flag):
    idx = cmd.index(flag)
    return cmd[idx + 1]


class MultiGatewayCommitmentTokenScriptTests(unittest.TestCase):
    def test_shared_state_gateways_receive_same_commitment_secret(self):
        cmds = []
        procs = []

        old_find = mgw.find_or_build_gateway
        old_wait = mgw.wait_for_gateway
        old_popen = mgw.subprocess.Popen
        old_log_dir = mgw.LOG_DIR

        def fake_popen(cmd, **_kwargs):
            proc = FakeProc(cmd)
            cmds.append(cmd)
            procs.append(proc)
            return proc

        with tempfile.TemporaryDirectory() as tmp:
            try:
                mgw.LOG_DIR = tmp
                mgw.find_or_build_gateway = lambda: os.path.join(tmp, "gateway-test")
                mgw.wait_for_gateway = lambda _port, _proc: True
                mgw.subprocess.Popen = fake_popen

                gw_a = mgw.start_plangate_gateway(
                    9601,
                    "gw-a:9601",
                    True,
                    "127.0.0.1:6379",
                    "test-a",
                    "optional",
                    "shared-secret",
                )
                gw_b = mgw.start_plangate_gateway(
                    9602,
                    "gw-b:9602",
                    True,
                    "127.0.0.1:6379",
                    "test-b",
                    "optional",
                    "shared-secret",
                )
            finally:
                mgw.find_or_build_gateway = old_find
                mgw.wait_for_gateway = old_wait
                mgw.subprocess.Popen = old_popen
                mgw.LOG_DIR = old_log_dir
                for proc in procs:
                    log_file = getattr(proc, "_log_file", None)
                    if log_file:
                        log_file.close()

        self.assertIs(gw_a, procs[0])
        self.assertIs(gw_b, procs[1])
        self.assertEqual(2, len(cmds))
        for cmd in cmds:
            self.assertEqual("optional", arg_value(cmd, "--commitment-token-mode"))
            self.assertEqual("shared-secret", arg_value(cmd, "--commitment-token-secret"))
            self.assertEqual("redis", arg_value(cmd, "--plangate-state-store"))

    def test_off_mode_does_not_pass_commitment_secret(self):
        cmds = []
        procs = []

        old_find = mgw.find_or_build_gateway
        old_wait = mgw.wait_for_gateway
        old_popen = mgw.subprocess.Popen
        old_log_dir = mgw.LOG_DIR

        def fake_popen(cmd, **_kwargs):
            proc = FakeProc(cmd)
            cmds.append(cmd)
            procs.append(proc)
            return proc

        with tempfile.TemporaryDirectory() as tmp:
            try:
                mgw.LOG_DIR = tmp
                mgw.find_or_build_gateway = lambda: os.path.join(tmp, "gateway-test")
                mgw.wait_for_gateway = lambda _port, _proc: True
                mgw.subprocess.Popen = fake_popen

                mgw.start_plangate_gateway(
                    9601,
                    "gw-a:9601",
                    False,
                    "127.0.0.1:6379",
                    "test-off",
                    "off",
                    "ignored-secret",
                )
            finally:
                mgw.find_or_build_gateway = old_find
                mgw.wait_for_gateway = old_wait
                mgw.subprocess.Popen = old_popen
                mgw.LOG_DIR = old_log_dir
                for proc in procs:
                    log_file = getattr(proc, "_log_file", None)
                    if log_file:
                        log_file.close()

        self.assertEqual(1, len(cmds))
        self.assertEqual("off", arg_value(cmds[0], "--commitment-token-mode"))
        self.assertNotIn("--commitment-token-secret", cmds[0])

    def test_strict_shared_mode_requires_explicit_secret(self):
        with self.assertRaises(ValueError):
            mgw.resolve_commitment_token_secret(["shared_random"], "strict", "")


if __name__ == "__main__":
    unittest.main()
