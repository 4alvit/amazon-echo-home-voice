"""Synthetic coverage for concise reports, optional metrics, touch and retry boundaries."""

import json
import os
from pathlib import Path
import ssl
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from amazon_echo_home_voice import gateway, lambda_handler, visuals
from amazon_echo_home_voice.accounts import UnlinkedAccount
from test_skill import event, payload, SKILL_ID, NOW
from test_visuals import screen_event, screen_data


def metric(value=72.5, unit="%", age=7, status="fresh"):
    return {"value": value, "unit": unit, "status": status, "age_seconds": age,
            "sources": ["synthetic-source"]}


def touch(action="refresh", report="status", detail="brief"):
    value = screen_event(kind="Alexa.Presentation.APL.UserEvent")
    value["request"].update(arguments=[action], token=visuals.TOKEN_PREFIX + report + ":" + detail,
                            source={"type": "TouchWrapper", "handler": "Press", "id": "energy_" + action})
    del value["request"]["intent"]
    return value


@patch.dict(os.environ, {"ASK_SKILL_ID": SKILL_ID, "IGW_URL": "https://igw.example/v1/energy",
                         "IGW_READ_TOKEN": "synthetic-token"}, clear=True)
class ExperienceTests(unittest.TestCase):
    @patch.object(lambda_handler, "fetch_energy")
    def test_default_brief_full_details_and_legacy_fallback(self, fetch):
        data = payload()
        data["reports"]["status"]["brief_text"] = "Short status with central warnings."
        fetch.return_value = data
        for value in (event(kind="LaunchRequest"), event("StatusIntent")):
            result = lambda_handler.lambda_handler(value, None)
            self.assertEqual(result["response"]["outputSpeech"]["text"], "Short status with central warnings.")
            self.assertTrue(result["response"]["shouldEndSession"])
            self.assertNotIn("reprompt", result["response"])
        result = lambda_handler.lambda_handler(event("DetailsIntent"), None)
        self.assertEqual(result["response"]["outputSpeech"]["text"], "Central status report.")
        del data["reports"]["status"]["brief_text"]
        self.assertEqual(lambda_handler.lambda_handler(event(kind="LaunchRequest"), None)
                         ["response"]["outputSpeech"]["text"], "Central status report.")

    @patch.object(lambda_handler, "fetch_energy")
    def test_repeat_uses_report_selector_and_refetches_instead_of_replaying_telemetry(self, fetch):
        fetch.return_value = payload()
        first = lambda_handler.lambda_handler(event("SolarTodayIntent"), None)
        self.assertEqual(first["sessionAttributes"], {"energy_report": "solar_today", "energy_detail": False})
        repeat = event("AMAZON.RepeatIntent")
        repeat["session"]["attributes"] = first["sessionAttributes"] | {"text": "Untrusted cached telemetry"}
        fetch.return_value["reports"]["solar_today"]["text"] = "Updated central report."
        second = lambda_handler.lambda_handler(repeat, None)
        self.assertEqual(second["response"]["outputSpeech"]["text"], "Updated central report.")
        self.assertEqual(fetch.call_count, 2)
        self.assertNotIn("Untrusted", json.dumps(second))
        repeat["session"]["attributes"] = {"energy_report": "../../other", "energy_detail": "true"}
        self.assertEqual(lambda_handler.lambda_handler(repeat, None)["sessionAttributes"]["energy_report"], "status")

    @patch.object(lambda_handler, "fetch_energy")
    def test_touch_actions_use_current_snapshot_and_central_details(self, fetch):
        fetch.return_value = payload()
        fetch.return_value["reports"]["status"]["brief_text"] = "Short central summary."
        choices = (("refresh", "status", "Short central summary."),
                   ("battery", "battery", "Central battery report."),
                   ("today", "solar_today", "Central solar_today report."),
                   ("details", "status", "Central status report."))
        for action, report, expected in choices:
            result = lambda_handler.lambda_handler(touch(action), None)
            self.assertEqual(result["sessionAttributes"]["energy_report"], report)
            self.assertEqual(result["response"]["outputSpeech"]["text"], expected)
            self.assertNotIn("reprompt", result["response"])
            self.assertNotIn("shouldEndSession", result["response"])
        self.assertEqual(fetch.call_count, 4)
        result = lambda_handler.lambda_handler(touch("refresh", "status", "full"), None)
        self.assertEqual(result["response"]["outputSpeech"]["text"], "Central status report.")

    @patch.object(lambda_handler, "fetch_energy")
    @patch.object(lambda_handler, "household_connection")
    def test_malformed_touch_requests_fail_before_identity_or_gateway(self, connection, fetch):
        changes = ({"arguments": []}, {"arguments": ["refresh", "battery"]},
                   {"arguments": ["write"]}, {"arguments": [{}]}, {"token": "home-energy-message"},
                   {"token": visuals.TOKEN_PREFIX + "../../other:brief"},
                   {"token": visuals.TOKEN_PREFIX + "status:full:extra"},
                   {"source": {"type": "TouchWrapper", "handler": "Press", "id": "energy_battery"}},
                   {"source": []})
        with patch.dict(os.environ, {"ENERGY_VOICE_MODE": "multi_household"}):
            for change in changes:
                value = touch()
                value["request"].update(change)
                with self.subTest(change=change), self.assertRaises(PermissionError):
                    lambda_handler.lambda_handler(value, None)
            value = touch()
            del value["context"]["System"]["device"]
            with self.assertRaises(PermissionError):
                lambda_handler.lambda_handler(value, None)
        connection.assert_not_called()
        fetch.assert_not_called()

    @patch.object(lambda_handler, "fetch_energy")
    def test_every_touch_repeat_and_details_rechecks_household_authorization(self, fetch):
        with patch.dict(os.environ, {"ENERGY_VOICE_MODE": "multi_household"}), \
                patch.object(lambda_handler, "household_connection", side_effect=UnlinkedAccount()) as connection:
            for value in [touch(action) for action in visuals.ACTIONS] + [
                    event("AMAZON.RepeatIntent"), event("DetailsIntent"), event("EnergyFlowIntent")]:
                result = lambda_handler.lambda_handler(value, None)
                self.assertEqual(result["response"]["card"]["type"], "LinkAccount")
                self.assertNotIn("Current data", json.dumps(result))
            self.assertEqual(connection.call_count, 7)
        fetch.assert_not_called()

    @patch.object(lambda_handler, "fetch_energy")
    def test_large_numbers_and_source_receipt_age_use_optional_validated_fields(self, fetch):
        data = payload()
        data["metrics"] = {"battery_soc": metric(), "solar_power": metric(1800, "W", 2),
                           "solar_today": metric(4.21, "kWh", 60)}
        fetch.return_value = data
        result = lambda_handler.lambda_handler(screen_event("StatusIntent"), None)
        rows = screen_data(result)["reports"]
        self.assertEqual([row["value"] for row in rows], ["72.5 %", "1.8 kW", "4.21 kWh", ""])
        self.assertEqual(data["metrics"]["solar_power"]["value"], 1800)
        self.assertEqual(data["metrics"]["solar_power"]["unit"], "W")
        self.assertIn("7 seconds before this snapshot", rows[0]["age"])
        self.assertNotIn(str(NOW), json.dumps(screen_data(result)))
        self.assertIn("not measurement age", screen_data(result)["footer"])
        self.assertEqual(result["response"]["outputSpeech"]["text"], "Central status report.")

    @patch.object(lambda_handler, "fetch_energy")
    def test_legacy_invalid_and_nonfresh_metrics_never_invent_a_numeric_reading(self, fetch):
        changes = ({}, None, metric(True), metric("72"), metric(float("nan")), metric(float("inf")),
                   metric(10**1000), metric(-1), metric(101), metric(unit="W"), metric(age=True),
                   metric(age=-1), metric(age=None), metric(age=2**64), metric(status="stale"))
        for sample in changes:
            data = payload()
            data["metrics"]["battery_soc"] = sample
            fetch.return_value = data
            with self.subTest(sample_type=type(sample).__name__):
                result = lambda_handler.lambda_handler(screen_event(), None)
                self.assertEqual(screen_data(result)["reports"][0]["value"], "")
                self.assertEqual(result["response"]["outputSpeech"]["text"], "Central battery report.")
        data = payload()
        data["metrics"]["battery_soc"] = metric(None, status="stale", age=180)
        data["reports"]["battery"]["status"] = "stale"
        fetch.return_value = data
        row = screen_data(lambda_handler.lambda_handler(screen_event(), None))["reports"][0]
        self.assertEqual(row["value"], "")
        self.assertEqual(row["status"], "Stale data")
        self.assertIn("3 minutes", row["age"])

    @patch.object(lambda_handler, "fetch_energy")
    def test_optional_flow_is_helpful_on_old_gateway_and_signed_on_new_gateway(self, fetch):
        data = payload()
        del data["reports"]["flow"]
        fetch.return_value = data
        result = lambda_handler.lambda_handler(screen_event("EnergyFlowIntent"), None)
        self.assertEqual(result["response"]["outputSpeech"]["text"], lambda_handler.FLOW_UNCONFIGURED_TEXT)
        self.assertFalse(screen_data(result)["interactive"])
        data["reports"]["flow"] = {"status": "fresh", "text": "Central power flow report."}
        data["metrics"].update(load_power=metric(1250, "W"), grid_power=metric(-500, "W"),
                               battery_power=metric(750, "W"))
        result = lambda_handler.lambda_handler(screen_event("EnergyFlowIntent"), None)
        rows = screen_data(result)["reports"]
        self.assertEqual([row["value"] for row in rows], ["", "1.25 kW", "-500 W", "750 W"])
        self.assertIn("+ import / - export", rows[2]["title"])
        self.assertIn("+ charging / - discharging", rows[3]["title"])
        self.assertEqual(result["response"]["outputSpeech"]["text"], "Central power flow report.")


class AdditiveContractTests(unittest.TestCase):
    def test_rust_generated_flow_fixture_preserves_central_speech_and_signs(self):
        # This synthetic fixture is checked against inverter-gateway's Rust engine.
        data = json.loads((Path(__file__).parent / "fixtures" / "energy-flow-v1.json").read_text())
        gateway.validate_payload(data, now=data["generated_at"], max_age_seconds=30)
        response = lambda_handler._speech(data["reports"]["flow"]["text"])
        result = visuals.report_visuals(screen_event("EnergyFlowIntent"), response, "flow", data)
        self.assertEqual(result["response"]["outputSpeech"]["text"], data["reports"]["flow"]["text"])
        self.assertEqual([row["value"] for row in screen_data(result)["reports"]],
                         ["", "1.25 kW", "-500 W", "750 W"])
        self.assertLessEqual(len(json.dumps(result, separators=(",", ":")).encode()), visuals.MAX_RESPONSE_BYTES)

    def test_power_units_are_presentation_only_and_large_values_are_bounded(self):
        for value, expected in ((10000, "10 kW"), (-12500, "-12.5 kW"), (0.001, "0.001 W")):
            self.assertEqual(visuals._metric_text(metric(value, "W"))[0], expected)
        self.assertLess(len(visuals._metric_text(metric(1.7e308, "W"))[0]), 24)

    def test_brief_text_and_optional_flow_are_validated_as_speech(self):
        for invalid in (None, "", "\n", "<speak>unsafe</speak>", "x" * 1201, True):
            data = payload()
            data["reports"]["status"]["brief_text"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(gateway.GatewayError):
                gateway.validate_payload(data, now=NOW, max_age_seconds=30)
        data = payload()
        data["reports"]["status"]["brief_text"] = "Central brief summary."
        self.assertEqual(gateway.validate_payload(data, now=NOW, max_age_seconds=30), data)
        data["reports"]["flow"]["text"] = "<invalid>"
        with self.assertRaises(gateway.GatewayError):
            gateway.validate_payload(data, now=NOW, max_age_seconds=30)


class TransientRetryTests(unittest.TestCase):
    def setUp(self):
        self.config = gateway.GatewayConfig("https://igw.example/v1/energy", "synthetic-token")

    def test_retries_only_once_and_reduces_timeout_to_original_budget(self):
        with patch.object(gateway.time, "monotonic", side_effect=[100, 101.5, 102]), \
                patch.object(gateway, "_fetch_once", side_effect=[gateway.TransientGatewayError("Gateway request failed"), payload()]) as fetch:
            self.assertEqual(gateway.fetch_energy(self.config), payload())
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args_list[1].args[0].timeout_seconds, 1.5)
        for elapsed in (2.95, 3, 4):
            with patch.object(gateway.time, "monotonic", side_effect=[100, 100 + elapsed]), \
                    patch.object(gateway, "_fetch_once", side_effect=gateway.TransientGatewayError("Gateway request failed")) as fetch, \
                    self.assertRaises(gateway.GatewayError):
                gateway.fetch_energy(self.config)
            self.assertEqual(fetch.call_count, 1)

    def test_http_and_transport_retry_classification(self):
        for error, expected_calls in (
            (HTTPError(self.config.url, 503, "unavailable", {}, None), 2),
            (HTTPError(self.config.url, 502, "bad gateway", {}, None), 2),
            (HTTPError(self.config.url, 504, "gateway timeout", {}, None), 2),
            (TimeoutError(), 2), (ConnectionResetError(), 2),
            (URLError(ConnectionRefusedError()), 2),
            (HTTPError(self.config.url, 401, "auth", {}, None), 1),
            (HTTPError(self.config.url, 403, "forbidden", {}, None), 1),
            (HTTPError(self.config.url, 429, "rate limit", {}, None), 1),
            (HTTPError(self.config.url, 500, "server error", {}, None), 1),
            (HTTPError(self.config.url, 302, "redirect", {}, None), 1),
            (ssl.SSLCertVerificationError(), 1), (URLError("unclassified"), 1),
        ):
            opener = Mock()
            opener.open.side_effect = error
            with self.subTest(error=type(error).__name__, expected_calls=expected_calls), \
                    self.assertRaises(gateway.GatewayError):
                gateway.fetch_energy(self.config, opener=opener)
            self.assertEqual(opener.open.call_count, expected_calls)

    def test_invalid_response_does_not_retry_and_public_policy_stays_enabled(self):
        response = Mock(status=200, headers={"Content-Type": "application/json"})
        response.read.return_value = b"not json"
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        with self.assertRaises(gateway.GatewayError):
            gateway.fetch_energy(self.config, opener=opener)
        self.assertEqual(opener.open.call_count, 1)
        public = gateway.GatewayConfig(self.config.url, "synthetic-token", public_only=True)
        body = json.dumps(payload()).encode()
        with patch.object(gateway, "_public_body", side_effect=[TimeoutError(), body]) as fetch:
            self.assertEqual(gateway.fetch_energy(public, now=NOW), payload())
        self.assertEqual(fetch.call_count, 2)
        self.assertTrue(all(call.args[0].public_only for call in fetch.call_args_list))

    def test_stalled_personal_transport_has_total_deadline_and_bounded_workers(self):
        release = threading.Event()
        entered = threading.Event()
        slots = threading.BoundedSemaphore(1)
        opener = Mock()
        late_response = Mock()

        def stall(*args, **kwargs):
            entered.set()
            release.wait(2)
            raise HTTPError(self.config.url, 403, "denied", {}, late_response)

        opener.open.side_effect = stall
        config = gateway.GatewayConfig(self.config.url, "synthetic-token", timeout_seconds=0.1)
        try:
            with patch.object(gateway, "_PERSONAL_REQUEST_SLOTS", slots):
                before = time.monotonic()
                with self.assertRaisesRegex(gateway.GatewayError, "timed out"):
                    gateway.fetch_energy(config, opener=opener)
                self.assertTrue(entered.is_set())
                self.assertLess(time.monotonic() - before, 0.5)
                for _ in range(3):
                    with self.assertRaisesRegex(gateway.GatewayError, "busy"):
                        gateway.fetch_energy(config, opener=opener)
                self.assertEqual(opener.open.call_count, 1)
                release.set()
                self.assertTrue(slots.acquire(timeout=1))
                slots.release()
                late_response.close.assert_called_once()
        finally:
            release.set()


if __name__ == "__main__":
    unittest.main()
