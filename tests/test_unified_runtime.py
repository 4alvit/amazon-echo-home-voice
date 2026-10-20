"""Configuration and health boundaries for the optional shared application image."""

import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "deploy/unified" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runtime = load("unified_runtime", "supervisor.py")
healthcheck = load("unified_healthcheck", "healthcheck.py")


class UnifiedRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.alexa = self.root / "alexa.env"
        self.cast = self.root / "cast.env"
        self.alexa.write_text("ASK_SKILL_ID=test-skill\nIGW_URL=https://gateway.example/v1/energy\n"
                              "IGW_READ_TOKEN=alexa-only\n")
        self.cast.write_text("IGW_URL=https://gateway.example/v1/energy\nIGW_READ_TOKEN=cast-only\n"
                             "CAST_API_TOKEN=cast-api-only\nCAST_BIND_HOST=192.168.10.20\n"
                             "CAST_MEDIA_BASE_URL=http://192.168.10.20:8091\n"
                             "CAST_HOST=192.168.10.30\nCAST_UUID=synthetic\n")
        self.env = {"UNIFIED_ALEXA_ENV_FILE": str(self.alexa),
                    "UNIFIED_CAST_ENV_FILE": str(self.cast)}

    def test_children_have_separate_credentials_and_no_inherited_injection(self):
        self.env.update({"LD_PRELOAD": "/evil", "PYTHONPATH": "/evil",
                         "GUNICORN_CMD_ARGS": "--bind 0.0.0.0", "UNRELATED_SECRET": "secret"})
        alexa, cast = runtime.child_specs(self.env)
        self.assertEqual(alexa.environment["IGW_READ_TOKEN"], "alexa-only")
        self.assertEqual(cast.environment["IGW_READ_TOKEN"], "cast-only")
        for child in (alexa, cast):
            for key in ("LD_PRELOAD", "PYTHONPATH", "GUNICORN_CMD_ARGS", "UNRELATED_SECRET"):
                self.assertNotIn(key, child.environment)
        self.assertNotIn("CAST_API_TOKEN", alexa.environment)
        self.assertNotIn("ASK_SKILL_ID", cast.environment)
        self.assertEqual((alexa.host, alexa.port), ("127.0.0.1", 8091))
        self.assertEqual((cast.host, cast.port), ("192.168.10.20", 8091))

    def test_feature_flags_do_not_read_disabled_config(self):
        self.env.update(UNIFIED_ENABLE_CAST="false", UNIFIED_CAST_ENV_FILE="/missing")
        self.assertEqual([c.name for c in runtime.child_specs(self.env)], ["alexa"])
        self.env["UNIFIED_ENABLE_ALEXA"] = "false"
        with self.assertRaises(runtime.ConfigurationError):
            runtime.child_specs(self.env)

    def test_unknown_duplicate_and_cross_app_settings_fail_closed(self):
        for line in ("ASK_SKILL_ID=again", "CAST_API_TOKEN=not-for-alexa", "export BAD=value",
                     "IGW_URL=second"):
            with self.subTest(line=line):
                self.alexa.write_text("ASK_SKILL_ID=test\nIGW_URL=https://example.com\n"
                                      "IGW_READ_TOKEN=test\n" + line + "\n")
                with self.assertRaises(runtime.ConfigurationError):
                    runtime.child_specs(self.env)

    def test_values_are_literal_not_shell_expanded(self):
        self.alexa.write_text("IGW_READ_TOKEN=$(do-not-run)$HOME`command`\n")
        parsed = runtime.read_env(str(self.alexa), runtime.ALEXA_KEYS)
        self.assertEqual(parsed["IGW_READ_TOKEN"], "$(do-not-run)$HOME`command`")

    def test_rejects_multihousehold_and_wildcard_bind(self):
        with self.alexa.open("a") as stream:
            stream.write("ENERGY_VOICE_MODE=multi_household\n")
        with self.assertRaises(runtime.ConfigurationError):
            runtime.child_specs(self.env)
        for host in ("0.0.0.0", "192.168.1.1", "::", "localhost"):
            with self.subTest(host=host), self.assertRaises(runtime.ConfigurationError):
                runtime.ipv4(host, loopback=True)
        for host in ("0.0.0.0", "127.0.0.1", "8.8.8.8", "224.1.1.1", "169.254.1.1"):
            with self.subTest(host=host), self.assertRaises(runtime.ConfigurationError):
                runtime.ipv4(host, loopback=False)

    def test_cast_media_origin_must_match_bound_address(self):
        self.cast.write_text(self.cast.read_text().replace(
            "http://192.168.10.20:8091", "http://192.168.10.99:8091"))
        with self.assertRaises(runtime.ConfigurationError):
            runtime.child_specs(self.env)

    def test_kitten_requires_existing_ca(self):
        with self.cast.open("a") as stream:
            stream.write("CAST_TTS_PROVIDER=kitten\nCAST_KITTEN_CA_FILE=/missing.pem\n")
        with self.assertRaises(runtime.ConfigurationError):
            runtime.child_specs(self.env)

    def test_occupied_listener_fails_before_start(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            child = runtime.Child("test", (), {}, "127.0.0.1", port, "/health")
            with self.assertRaises(runtime.ConfigurationError):
                runtime.check_free_ports([child])

    def test_completed_connection_time_wait_allows_restart(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            with socket.create_connection(("127.0.0.1", port)) as client:
                server, _ = listener.accept()
                server.close()  # Server side owns TIME_WAIT after the FIN exchange.
                self.assertEqual(client.recv(1), b"")
        child = runtime.Child("test", (), {}, "127.0.0.1", port, "/health")
        runtime.check_free_ports([child])

    def test_status_stale_unhealthy_and_dead_process_are_rejected(self):
        path = self.root / "status.json"
        self.assertFalse(healthcheck.healthy(path))
        runtime.write_status(path, True, {"alexa": True, "cast": True})
        self.assertTrue(healthcheck.healthy(path))
        data = json.loads(path.read_text())
        self.assertEqual(set(data), {"ready", "pid", "updated", "children"})
        for update in ({"updated": time.monotonic() - 60}, {"ready": False},
                       {"children": {"cast": False}}, {"children": {}}):
            path.write_text(json.dumps(data | update))
            self.assertFalse(healthcheck.healthy(path))
        path.write_text(json.dumps(data))
        with patch.object(healthcheck.os, "kill", side_effect=ProcessLookupError):
            self.assertFalse(healthcheck.healthy(path))


if __name__ == "__main__":
    unittest.main()
