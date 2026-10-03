"""CLI optional-flow handling without env credential reads or network."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from amazon_echo_home_voice import cli, gateway


NOW = 1800000000


def core_only_payload():
    """Valid older gateway payload: five core reports, no optional flow."""
    return {
        "schema_version": 1,
        "generated_at": NOW,
        "mqtt_connected": True,
        "metrics": {"battery_soc": {}, "solar_power": {}, "solar_today": {}},
        "reports": {
            name: {"status": "fresh", "text": f"Central {name} report."}
            for name in gateway.REPORT_NAMES
        },
    }


def fresh_flow_payload():
    data = core_only_payload()
    data["reports"]["flow"] = {"status": "fresh", "text": "Central power flow report."}
    return data


class CliOptionalFlowTests(unittest.TestCase):
    def setUp(self):
        # Never read real env credentials: from_env is fully mocked.
        self.config = gateway.GatewayConfig("https://igw.example/v1/energy", "test-read-token")

    def _run_flow(self, payload):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(gateway.GatewayConfig, "from_env", return_value=self.config), \
             patch.object(cli, "fetch_energy", return_value=payload), \
             patch("sys.argv", ["energy-voice", "flow"]), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_core_only_valid_payload_flow_is_unconfigured_exit_2(self):
        data = core_only_payload()
        # Gateway still accepts older payloads without optional flow.
        gateway.validate_payload(data, now=NOW, max_age_seconds=30)
        self.assertNotIn("flow", data["reports"])

        code, out, err = self._run_flow(data)
        self.assertEqual(code, 2)
        self.assertEqual(out.strip(), gateway.FLOW_UNCONFIGURED_TEXT)
        self.assertIn("Report status: unconfigured", err)
        self.assertNotIn("KeyError", err)
        self.assertNotIn("Traceback", err)

    def test_fresh_flow_payload_exits_0(self):
        data = fresh_flow_payload()
        gateway.validate_payload(data, now=NOW, max_age_seconds=30)
        code, out, err = self._run_flow(data)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "Central power flow report.")
        self.assertIn("Report status: fresh", err)

    def test_nonfresh_flow_preserves_status_and_exits_2(self):
        for status in ("stale", "unavailable", "unconfigured"):
            with self.subTest(status=status):
                data = fresh_flow_payload()
                data["reports"]["flow"]["status"] = status
                code, out, err = self._run_flow(data)
                self.assertEqual(code, 2)
                self.assertIn("Central power flow report.", out)
                self.assertIn(f"Report status: {status}", err)


if __name__ == "__main__":
    unittest.main()
