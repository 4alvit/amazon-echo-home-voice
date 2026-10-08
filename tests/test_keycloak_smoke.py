"""Exercise the security boundaries of the disposable OAuth test harness."""

import importlib.util
from pathlib import Path
import ssl
import subprocess
import unittest
from unittest.mock import patch

_PATH = Path(__file__).parent / "integration/keycloak_smoke.py"
_SPEC = importlib.util.spec_from_file_location("keycloak_smoke", _PATH)
smoke = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(smoke)


class SmokeBoundaryTests(unittest.TestCase):
    def test_upstream_header_names_cannot_inject_fields(self):
        for name in ("bad:field", "bad\r\nInjected", "", " leading", "trailing "):
            with self.subTest(name=name), self.assertRaises(ValueError):
                smoke.checked_response_headers([(name, "value")])

    def test_upstream_header_values_cannot_inject_fields(self):
        for value in ("safe\r\nInjected: yes", "safe\nInjected: yes", "safe\rInjected: yes"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                smoke.checked_response_headers([("Location", value)])

    def test_header_validation_rejects_delimiters_at_every_position(self):
        for delimiter in ("\r", "\n", "\r\n"):
            for index in range(len("Location") + 1):
                name = "Location"[:index] + delimiter + "Location"[index:]
                with self.subTest(name=name), self.assertRaises(ValueError):
                    smoke.checked_response_headers([(name, "value")])
            for index in range(len("https://example.test") + 1):
                value = "https://example.test"[:index] + delimiter + "https://example.test"[index:]
                with self.subTest(value=value), self.assertRaises(ValueError):
                    smoke.checked_response_headers([("Location", value)])

    def test_legitimate_redirect_and_duplicate_cookies_are_preserved(self):
        headers = [("Location", "https://example.test/callback?code=a&state=b"),
                   ("Set-Cookie", "a=1; Secure; HttpOnly"),
                   ("Set-Cookie", "b=2; Secure; HttpOnly")]
        self.assertEqual(smoke.checked_response_headers(headers), headers)

    def test_tls_rejects_obsolete_protocols(self):
        context = smoke.server_tls_context()
        self.assertGreaterEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_synthetic_environment_uses_stdin_and_not_command_line(self):
        environment = "POSTGRES_PASSWORD=synthetic-fixture\n"
        with patch.object(smoke.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "container\n", "")) as run:
            self.assertEqual(smoke.docker("run", "--env-file", "/dev/stdin", "image", environment=environment), "container")
        self.assertNotIn(environment, run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs["input"], environment)

    def test_docker_errors_do_not_expose_stderr_or_environment(self):
        with patch.object(smoke.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "sensitive diagnostic")):
            with self.assertRaisesRegex(RuntimeError, "^A disposable Docker run command failed\\.$"):
                smoke.docker("run", environment="synthetic-fixture")
