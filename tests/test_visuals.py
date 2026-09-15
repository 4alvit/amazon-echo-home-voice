"""Visual responses use synthetic reports and never require a real screen."""

import json
import os
import unittest
from unittest.mock import patch

from amazon_echo_home_voice import gateway, lambda_handler, visuals
from amazon_echo_home_voice.accounts import UnlinkedAccount
from test_skill import event, payload, SKILL_ID


def screen_event(name="BatteryIntent", kind="IntentRequest"):
    value = event(name, kind)
    value["context"]["System"]["device"] = {
        "supportedInterfaces": {visuals.APL_INTERFACE: {"runtime": {"maxVersion": "1.0"}}},
    }
    return value


def screen_data(response):
    directive, = response["response"]["directives"]
    return directive["datasources"]["energy"]


@patch.dict(os.environ, {
    "ASK_SKILL_ID": SKILL_ID, "IGW_URL": "https://igw.example/v1/energy",
    "IGW_READ_TOKEN": "synthetic-token",
}, clear=True)
class VisualSkillTests(unittest.TestCase):
    @patch.object(lambda_handler, "fetch_energy", side_effect=lambda _: payload())
    def test_each_screen_report_uses_same_snapshot_and_unchanged_speech(self, fetch):
        for intent, name in lambda_handler.INTENTS.items():
            with self.subTest(intent=intent):
                response = lambda_handler.lambda_handler(screen_event(intent), None)
                body = response["response"]
                self.assertEqual(body["outputSpeech"]["text"], f"Central {name} report.")
                self.assertNotIn("shouldEndSession", body)
                self.assertNotIn("reprompt", body)
                self.assertEqual(body["card"]["type"], "Simple")
                self.assertEqual(body["directives"][0]["document"]["version"], "1.0")
                expected = ("battery", "solar", "solar_today", "alarms") if name == "status" else (name,)
                self.assertEqual([report["text"] for report in screen_data(response)["reports"]],
                                 [f"Central {key} report." for key in expected])
                self.assertEqual(fetch.call_count, 1)
                fetch.reset_mock()

    @patch.object(lambda_handler, "fetch_energy", side_effect=lambda _: payload())
    def test_voice_only_and_malformed_interface_descriptions_have_no_directive(self, fetch):
        devices = [None, [], "screen", {}, {"supportedInterfaces": None},
                   {"supportedInterfaces": [visuals.APL_INTERFACE]},
                   {"supportedInterfaces": {visuals.APL_INTERFACE: None}},
                   {"supportedInterfaces": {visuals.APL_INTERFACE: True}},
                   {"supportedInterfaces": {"Display": {}}}]
        for device in devices:
            value = event()
            value["context"]["System"]["device"] = device
            with self.subTest(device=device):
                body = lambda_handler.lambda_handler(value, None)["response"]
                self.assertNotIn("directives", body)
                self.assertEqual(body["outputSpeech"]["text"], "Central battery report.")
                self.assertTrue(body["shouldEndSession"])
                self.assertEqual(body["card"]["type"], "Simple")
        self.assertFalse(visuals.supports_apl({"context": {"System": None}}))
        self.assertTrue(visuals.supports_apl({"context": {"System": {"device": {
            "supportedInterfaces": {visuals.APL_INTERFACE: {}},
        }}}}))

    @patch.object(lambda_handler, "fetch_energy")
    def test_freshness_labels_and_central_caveats_are_preserved(self, fetch):
        data = payload()
        for name, status in zip(("battery", "solar", "solar_today", "alarms"), gateway.STATUSES):
            data["reports"][name] = {"status": status, "text": f"Central {name} caveat: {status}."}
        fetch.return_value = data
        reports = screen_data(lambda_handler.lambda_handler(screen_event("StatusIntent"), None))["reports"]
        for report, name in zip(reports, ("battery", "solar", "solar_today", "alarms")):
            central = data["reports"][name]
            self.assertEqual(report["status"], visuals.STATUS_LABELS[central["status"]])
            self.assertEqual(report["text"], central["text"])

    @patch.object(lambda_handler, "fetch_energy")
    def test_entities_and_expressions_are_literal_data_not_document_code(self, fetch):
        central_text = "Value ${2+2}, &lt;b&gt; and &#36;{evil}: $5 & 6."
        data = payload()
        data["reports"]["battery"]["text"] = central_text
        gateway.validate_payload(data, now=data["generated_at"], max_age_seconds=30)
        fetch.return_value = data
        response = lambda_handler.lambda_handler(screen_event(), None)
        self.assertEqual(response["response"]["outputSpeech"]["text"], central_text)
        self.assertIn(central_text, response["response"]["card"]["content"])
        displayed = screen_data(response)["reports"][0]["text"]
        self.assertEqual(displayed, "Value &#36;{2+2}, &amp;lt;b&amp;gt; and &amp;#36;{evil}: &#36;5 &amp; 6.")
        document = json.dumps(response["response"]["directives"][0]["document"])
        self.assertNotIn(central_text, document)
        self.assertNotIn("eval(", document)

    @patch.object(lambda_handler, "fetch_energy")
    def test_resource_reference_is_literal_text(self, fetch):
        data = payload()
        data["reports"]["battery"]["text"] = "@viewportResource"
        fetch.return_value = data
        response = lambda_handler.lambda_handler(screen_event(), None)
        self.assertEqual(response["response"]["outputSpeech"]["text"], "@viewportResource")
        self.assertEqual(screen_data(response)["reports"][0]["text"], "&#64;viewportResource")

    @patch.object(lambda_handler, "fetch_energy")
    def test_gateway_failure_replaces_readings_with_safe_unavailability(self, fetch):
        fetch.return_value = payload()
        first = lambda_handler.lambda_handler(screen_event(), None)
        fetch.side_effect = gateway.GatewayError("synthetic-private-host-and-token")
        second = lambda_handler.lambda_handler(screen_event(), None)
        self.assertEqual(screen_data(second)["reports"][0]["text"], gateway.UNAVAILABLE_TEXT)
        self.assertEqual(screen_data(second)["reports"][0]["status"], "Data unavailable")
        self.assertNotIn("Central battery report", json.dumps(second))
        self.assertNotIn("synthetic-private", json.dumps(second))
        self.assertEqual(screen_data(first)["reports"][0]["text"], "Central battery report.")

    @patch.object(lambda_handler, "fetch_energy")
    def test_welcome_help_and_stop_have_appropriate_session_behavior(self, fetch):
        for value in (screen_event(kind="LaunchRequest"), screen_event("AMAZON.HelpIntent"),
                      screen_event("AMAZON.FallbackIntent"), screen_event("SetModeIntent")):
            response = lambda_handler.lambda_handler(value, None)
            self.assertIn("directives", response["response"])
            self.assertFalse(response["response"]["shouldEndSession"])
            self.assertIn("reprompt", response["response"])
        for name in ("AMAZON.StopIntent", "AMAZON.CancelIntent"):
            response = lambda_handler.lambda_handler(screen_event(name), None)
            self.assertEqual(response["response"], {
                "outputSpeech": {"type": "PlainText", "text": "Goodbye."}, "shouldEndSession": True,
            })
        self.assertEqual(lambda_handler.lambda_handler(screen_event(kind="SessionEndedRequest"), None)["response"], {})
        fetch.assert_not_called()

    @patch.object(lambda_handler, "fetch_energy")
    def test_unlinked_screen_retains_link_account_card_and_cannot_show_reports(self, fetch):
        with patch.dict(os.environ, {"ENERGY_VOICE_MODE": "multi_household"}), \
             patch.object(lambda_handler, "household_connection", side_effect=UnlinkedAccount()):
            for value in (screen_event(), screen_event(kind="LaunchRequest")):
                response = lambda_handler.lambda_handler(value, None)
                self.assertEqual(response["response"]["card"], {"type": "LinkAccount"})
                self.assertIn("link your Home Energy account", screen_data(response)["reports"][0]["text"])
                self.assertNotIn("Current data", json.dumps(response))
        fetch.assert_not_called()

    @patch.object(lambda_handler, "fetch_energy")
    def test_worst_case_valid_text_remains_within_relay_limit_without_shortening_speech(self, fetch):
        for text in ("x" * 1200, "&" * 1200, "$" * 1200, "@" * 1200, "\U0001f50b" * 1200, '"\\' * 600):
            data = payload()
            for report in data["reports"].values():
                report["text"] = text
            gateway.validate_payload(data, now=data["generated_at"], max_age_seconds=30)
            fetch.return_value = data
            for build_event in (event, screen_event):
                for intent in ("BatteryIntent", "StatusIntent"):
                    with self.subTest(text=text[:3], screen=build_event.__name__, intent=intent):
                        response = lambda_handler.lambda_handler(build_event(intent), None)
                        self.assertLessEqual(len(json.dumps(response, separators=(",", ":")).encode()), 16384)
                        self.assertEqual(response["response"]["outputSpeech"]["text"], text)
                        if "directives" in response["response"]:
                            self.assertTrue(all(report["status"] == "Current data" for report in screen_data(response)["reports"]))
                        else:
                            self.assertTrue(response["response"]["shouldEndSession"])


if __name__ == "__main__":
    unittest.main()
