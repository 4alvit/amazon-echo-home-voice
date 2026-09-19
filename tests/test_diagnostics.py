"""Failure diagnostics are useful without publishing private exception content."""

import json
import os
import ssl
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from amazon_echo_home_voice import diagnostics, gateway, lambda_handler
from test_skill import event, payload, SKILL_ID
from test_visuals import screen_data, screen_event


PRIVATE = "https://private.example/home?token=synthetic-secret\nprivate-report=123.4"


def fields(record):
    return json.loads(record.getMessage().removeprefix("energy_voice_diagnostic "))


class DiagnosticTests(unittest.TestCase):
    def record_failure(self, error):
        with self.assertLogs(diagnostics.LOGGER, level="WARNING") as logs:
            diagnostics.gateway_failure(error)
        self.assertEqual(len(logs.records), 1)
        self.assertIsNone(logs.records[0].exc_info)
        self.assertNotIn("private", logs.output[0])
        return fields(logs.records[0])

    def test_failure_categories_distinguish_configuration_deadline_and_contract(self):
        for message, category in (
            ("Invalid IGW_READ_TOKEN configuration", "configuration"),
            ("Voice request timed out", "deadline"),
            ("Gateway request timed out", "deadline"),
            ("Gateway must resolve only to public Internet addresses", "network_policy"),
            ("Gateway resolution failed", "resolver"),
            ("Gateway request failed", "transport"),
            ("Gateway returned an invalid content type", "response_format"),
            ("Gateway response is out of date", "envelope_age"),
            ("Inconsistent gateway report status", "report_contract"),
        ):
            with self.subTest(category=category, message=message):
                self.assertEqual(self.record_failure(gateway.GatewayError(message)), {
                    "event": "gateway_failure", "category": category, "cause": "none",
                })

    def test_http_errors_record_only_status_and_allowlisted_class(self):
        error = gateway.GatewayError("Gateway request failed")
        error.__cause__ = HTTPError(PRIVATE, 403, PRIVATE, {"Authorization": PRIVATE}, None)
        self.addCleanup(error.__cause__.close)
        self.assertEqual(self.record_failure(error), {
            "event": "gateway_failure", "category": "transport", "cause": "HTTPError", "http_status": 403,
        })
        for invalid_code in (True, 99, 600, PRIVATE):
            with self.subTest(code=type(invalid_code).__name__):
                error.__cause__.code = invalid_code
                self.assertNotIn("http_status", self.record_failure(error))

    def test_wrapped_timeout_and_tls_errors_do_not_format_their_messages(self):
        for cause, name in (
            (TimeoutError(PRIVATE), "TimeoutError"),
            (ssl.SSLCertVerificationError(PRIVATE), "SSLCertVerificationError"),
        ):
            with self.subTest(name=name):
                error = gateway.GatewayError("Gateway request failed")
                error.__cause__ = URLError(cause)
                self.assertEqual(self.record_failure(error)["cause"], name)

    def test_hostile_exception_text_and_class_name_are_never_formatted(self):
        class private_token_exception(Exception):
            def __str__(self):
                raise AssertionError("Exception text must not be formatted")

        error = gateway.GatewayError(PRIVATE)
        error.__cause__ = private_token_exception(PRIVATE)
        self.assertEqual(self.record_failure(error), {
            "event": "gateway_failure", "category": "unknown", "cause": "other",
        })


@patch.dict(os.environ, {
    "ASK_SKILL_ID": SKILL_ID, "IGW_URL": "https://igw.example/v1/energy",
    "IGW_READ_TOKEN": "synthetic-token",
}, clear=True)
class HandlerDiagnosticTests(unittest.TestCase):
    def test_gateway_failure_logs_once_and_preserves_the_safe_single_card(self):
        error = gateway.GatewayError("Gateway request failed")
        error.__cause__ = HTTPError(PRIVATE, 401, PRIVATE, {}, None)
        self.addCleanup(error.__cause__.close)
        with patch.object(lambda_handler, "fetch_energy", side_effect=error) as fetch, \
                self.assertLogs(diagnostics.LOGGER, level="WARNING") as logs:
            response = lambda_handler.lambda_handler(screen_event(kind="LaunchRequest"), None)
        fetch.assert_called_once()
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(fields(logs.records[0])["http_status"], 401)
        self.assertNotIn("private", "".join(logs.output))
        self.assertEqual(response["response"]["outputSpeech"]["text"], gateway.UNAVAILABLE_TEXT)
        self.assertEqual(len(screen_data(response)["reports"]), 1)
        self.assertEqual(screen_data(response)["reports"][0]["status"], "Data unavailable")
        self.assertNotIn("shouldEndSession", response["response"])

    def test_configuration_and_early_deadline_failures_are_logged_without_fetching(self):
        for setting, value, deadline, category in (
            ("ENERGY_VOICE_MODE", PRIVATE, None, "configuration"),
            ("IGW_READ_TOKEN", "", None, "configuration"),
            ("ENERGY_VOICE_MODE", "personal", 0, "deadline"),
        ):
            with self.subTest(category=category, setting=setting), \
                    patch.dict(os.environ, {setting: value}), \
                    patch.object(lambda_handler, "fetch_energy") as fetch, \
                    self.assertLogs(diagnostics.LOGGER, level="WARNING") as logs:
                response = lambda_handler.lambda_handler(event(kind="LaunchRequest"), None, deadline=deadline)
            fetch.assert_not_called()
            self.assertEqual(len(logs.records), 1)
            self.assertEqual(fields(logs.records[0])["category"], category)
            self.assertNotIn("private", "".join(logs.output))
            self.assertEqual(response["response"]["outputSpeech"]["text"], gateway.UNAVAILABLE_TEXT)
            self.assertTrue(response["response"]["shouldEndSession"])

    def test_non_fresh_reports_log_statuses_without_content_and_keep_four_cards(self):
        data = payload()
        del data["reports"]["flow"]  # Older gateways expose only the original five reports.
        data["mqtt_connected"] = False
        private_report = PRIVATE.replace("\n", " ")
        expected = dict(zip(gateway.REPORT_NAMES, ("stale", "unavailable", "unconfigured", "unavailable", "unavailable")))
        for name, status in expected.items():
            data["reports"][name] = {"status": status, "text": private_report}
        gateway.validate_payload(data, now=data["generated_at"], max_age_seconds=30)
        with patch.object(lambda_handler, "fetch_energy", return_value=data), \
                self.assertLogs(diagnostics.LOGGER, level="WARNING") as logs:
            response = lambda_handler.lambda_handler(screen_event(kind="LaunchRequest"), None)
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(fields(logs.records[0]), {
            "event": "upstream_report_status", "mqtt_connected": False, "reports": expected,
        })
        self.assertNotIn("private", "".join(logs.output))
        self.assertEqual(response["response"]["outputSpeech"]["text"], private_report)
        self.assertEqual(len(screen_data(response)["reports"]), 4)
        self.assertNotIn("shouldEndSession", response["response"])

    def test_fresh_reports_remain_quiet(self):
        with patch.object(lambda_handler, "fetch_energy", return_value=payload()) as fetch, \
                self.assertNoLogs(diagnostics.LOGGER, level="WARNING"):
            response = lambda_handler.lambda_handler(event(kind="LaunchRequest"), None)
        fetch.assert_called_once()
        self.assertEqual(response["response"]["outputSpeech"]["text"], "Central status report.")
        self.assertTrue(response["response"]["shouldEndSession"])

    def test_optional_flow_status_is_logged_without_its_text_or_sources(self):
        data = payload()
        data["reports"]["flow"] = {"status": "unconfigured", "text": PRIVATE.replace("\n", " ")}
        with patch.object(lambda_handler, "fetch_energy", return_value=data), \
                self.assertLogs(diagnostics.LOGGER, level="WARNING") as logs:
            lambda_handler.lambda_handler(event("EnergyFlowIntent"), None)
        self.assertEqual(fields(logs.records[0])["reports"]["flow"], "unconfigured")
        self.assertNotIn("private", "".join(logs.output))


if __name__ == "__main__":
    unittest.main()
