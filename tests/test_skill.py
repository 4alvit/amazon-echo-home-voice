from datetime import datetime, timezone
from http.client import IncompleteRead
import io
import json
import os
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request

from amazon_echo_home_voice import gateway, lambda_handler, webhook


SKILL_ID = "amzn1.ask.skill.test-id"
NOW = 1800000000


def payload():
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


def event(name="BatteryIntent", kind="IntentRequest"):
    return {
        "version": "1.0",
        "context": {"System": {"application": {"applicationId": SKILL_ID}}},
        "session": {"application": {"applicationId": SKILL_ID}},
        "request": {
            "type": kind,
            "locale": "en-US",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "intent": {"name": name},
        },
    }


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.config = gateway.GatewayConfig("https://igw.example/v1/energy", "test-read-token")

    def opener(self, body=None, status=200, content_type="application/json"):
        response = Mock()
        response.status = status
        response.headers = {"Content-Type": content_type}
        response.read.return_value = json.dumps(payload()).encode() if body is None else body
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = response
        return opener

    def test_reads_central_reports_with_scoped_auth_and_bounded_body(self):
        config = gateway.GatewayConfig(self.config.url, "read-only", "cf-id", "cf-secret")
        opener = self.opener()
        result = gateway.fetch_energy(config, opener=opener, now=NOW)
        self.assertEqual(result, payload())
        request = opener.open.call_args.args[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer read-only")
        self.assertEqual(request.get_header("Cf-access-client-id"), "cf-id")
        self.assertEqual(request.get_header("Cf-access-client-secret"), "cf-secret")
        opener.open.assert_called_once_with(request, timeout=3)
        opener.open.return_value.read.assert_called_once_with(gateway.MAX_RESPONSE_BYTES + 1)

    def test_transport_rejects_non_json_oversize_and_auth_errors(self):
        for opener in (
            self.opener(content_type="text/html"), self.opener(status=401),
            self.opener(body=b"x" * (gateway.MAX_RESPONSE_BYTES + 1)),
            self.opener(body=b'{"schema_version":1,"schema_version":1}'),
            self.opener(body=b"not json"), self.opener(body=b"\xff"),
        ):
            with self.subTest(opener=opener), self.assertRaises(gateway.GatewayError):
                gateway.fetch_energy(self.config, opener=opener, now=NOW)
        for error in (URLError("private address"), TimeoutError(), IncompleteRead(b"{"), HTTPError(self.config.url, 302, "redirect", {}, None)):
            opener = Mock()
            opener.open.side_effect = error
            with self.subTest(error=error), self.assertRaises(gateway.GatewayError) as caught:
                gateway.fetch_energy(self.config, opener=opener, now=NOW)
            self.assertNotIn("private address", str(caught.exception))

    def test_redirect_handler_never_builds_a_credentialled_redirect(self):
        request = Request(self.config.url, headers={"Authorization": "Bearer secret"})
        for target in ("https://evil.example/", "https://igw.example/new", "http://igw.example/"):
            self.assertIsNone(gateway.NoRedirects().redirect_request(request, None, 302, "", {}, target))

    def test_config_rejects_insecure_urls_header_injection_and_partial_cf_credentials(self):
        for url in ("http://igw.example/v1/energy", "https://user:pass@igw.example/v1/energy", "https://igw.example/v1/snapshot", "https://igw.example/v1/energy?x=1", "https://igw.example:8443/v1/energy", "https://igw.example/v1/energy#x"):
            with self.subTest(url=url), self.assertRaises(gateway.GatewayError):
                gateway.GatewayConfig(url, "token")
        for kwargs in ({"read_token": ""}, {"read_token": "a\r\nx:y"}, {"cf_client_id": "id"}, {"timeout_seconds": float("nan")}, {"max_age_seconds": 1000}):
            values = {"url": self.config.url, "read_token": "token"} | kwargs
            with self.subTest(kwargs=kwargs), self.assertRaises(gateway.GatewayError):
                gateway.GatewayConfig(**values)

    def test_rejects_old_future_and_invalid_schemas(self):
        changes = [
            {"generated_at": NOW - 31}, {"generated_at": NOW + 6},
            {"generated_at": float("nan")}, {"generated_at": True},
            {"schema_version": True}, {"schema_version": 2},
            {"mqtt_connected": "true"}, {"mqtt_connected": False},
            {"metrics": {}}, {"reports": []},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(gateway.GatewayError):
                gateway.validate_payload(payload() | change, now=NOW, max_age_seconds=30)

    def test_rejects_invalid_speech_and_unknown_status(self):
        for value in ("", " ", "x" * 1201, "<speak>fake</speak>", "text\nmore", None, 1):
            data = payload()
            data["reports"]["battery"]["text"] = value
            with self.subTest(value=value), self.assertRaises(gateway.GatewayError):
                gateway.validate_payload(data, now=NOW, max_age_seconds=30)
        data = payload()
        data["reports"]["battery"]["status"] = "unknown"
        with self.assertRaises(gateway.GatewayError):
            gateway.validate_payload(data, now=NOW, max_age_seconds=30)

    def test_stale_and_unavailable_reports_preserve_central_text(self):
        for status in ("stale", "unavailable", "unconfigured"):
            data = payload()
            data["mqtt_connected"] = False
            for report in data["reports"].values():
                report.update(status=status, text="Solar data is unavailable.")
            self.assertEqual(gateway.validate_payload(data, now=NOW, max_age_seconds=30), data)


@patch.dict(os.environ, {"ASK_SKILL_ID": SKILL_ID, "IGW_URL": "https://igw.example/v1/energy", "IGW_READ_TOKEN": "test-token"}, clear=True)
class LambdaTests(unittest.TestCase):
    @patch.object(lambda_handler, "fetch_energy")
    def test_each_intent_reads_its_report_without_calculation(self, fetch):
        fetch.return_value = payload()
        for intent, report in lambda_handler.INTENTS.items():
            result = lambda_handler.lambda_handler(event(intent), None)
            self.assertEqual(result["response"]["outputSpeech"], {"type": "PlainText", "text": f"Central {report} report."})
            self.assertTrue(result["response"]["shouldEndSession"])

    @patch.object(lambda_handler, "fetch_energy")
    def test_help_stop_unknown_and_session_end_never_fetch(self, fetch):
        for name in ("AMAZON.HelpIntent", "AMAZON.FallbackIntent", "AMAZON.StopIntent", "AMAZON.CancelIntent", "SetInverterModeIntent"):
            lambda_handler.lambda_handler(event(name), None)
        self.assertEqual(lambda_handler.lambda_handler(event(kind="SessionEndedRequest"), None)["response"], {})
        fetch.assert_not_called()

    @patch.object(lambda_handler, "fetch_energy")
    def test_launch_without_an_intent_reads_the_status_report_once(self, fetch):
        fetch.return_value = payload()
        value = event(kind="LaunchRequest")
        del value["request"]["intent"]
        result = lambda_handler.lambda_handler(value, None)
        fetch.assert_called_once()
        self.assertEqual(result["response"]["outputSpeech"]["text"], "Central status report.")
        self.assertTrue(result["response"]["shouldEndSession"])
        self.assertNotIn("reprompt", result["response"])

    @patch.object(lambda_handler, "fetch_energy", side_effect=gateway.GatewayError("private-details"))
    def test_launch_reports_gateway_unavailability_instead_of_a_welcome(self, fetch):
        result = lambda_handler.lambda_handler(event(kind="LaunchRequest"), None)
        fetch.assert_called_once()
        self.assertEqual(result["response"]["outputSpeech"]["text"], gateway.UNAVAILABLE_TEXT)
        self.assertNotIn("private-details", json.dumps(result))

    @patch.object(lambda_handler, "fetch_energy")
    def test_auth_and_expired_requests_fail_before_gateway(self, fetch):
        invalid = []
        value = event()
        value["context"]["System"]["application"]["applicationId"] = "other"
        invalid.append(value)
        value = event()
        value["session"]["application"]["applicationId"] = "other"
        invalid.append(value)
        for timestamp in ("invalid", "2020-01-01T00:00:00Z", datetime.now().isoformat()):
            value = event()
            value["request"]["timestamp"] = timestamp
            invalid.append(value)
        invalid.extend([{}, None, event(kind="AudioPlayer.PlaybackStarted")])
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(PermissionError):
                lambda_handler.lambda_handler(value, None)
        with patch.dict(os.environ, {"ASK_SKILL_ID": ""}), self.assertRaises(PermissionError):
            lambda_handler.lambda_handler(event(), None)
        fetch.assert_not_called()

    @patch.object(lambda_handler, "fetch_energy", side_effect=gateway.GatewayError("Auth failed"))
    def test_gateway_failure_speaks_understandable_unavailability(self, fetch):
        result = lambda_handler.lambda_handler(event(), None)
        self.assertEqual(result["response"]["outputSpeech"]["text"], gateway.UNAVAILABLE_TEXT)
        self.assertNotIn("Auth", json.dumps(result))


@patch.dict(os.environ, {"ASK_SKILL_ID": SKILL_ID}, clear=True)
class WebhookTests(unittest.TestCase):
    def call(self, value=None, headers=True, method="POST", path="/alexa", body=None):
        body = json.dumps(event() if value is None else value).encode() if body is None else body
        environ = {
            "PATH_INFO": path, "REQUEST_METHOD": method,
            "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": io.BytesIO(body),
        }
        if headers:
            environ.update(HTTP_SIGNATURECERTCHAINURL="https://s3.amazonaws.com/echo.api/test.pem", HTTP_SIGNATURE_256="fake-signature")
        start = Mock()
        response = b"".join(webhook.application(environ, start))
        return start.call_args.args[0], json.loads(response)

    @patch.object(webhook, "lambda_handler")
    @patch.object(webhook, "verify_signature_and_timestamp")
    def test_verified_request_is_dispatched_only_after_sdk_verification(self, verifier, handler):
        handler.return_value = {"version": "1.0", "response": {}}
        self.assertEqual(self.call()[0], "200 OK")
        verifier.assert_called_once()
        self.assertEqual(verifier.call_args.args[0]["signature-256"], "fake-signature")
        handler.assert_called_once()

    @patch.object(webhook, "lambda_handler")
    @patch.object(webhook, "verify_signature_and_timestamp", side_effect=ValueError("bad signature"))
    def test_invalid_signature_never_dispatches(self, verifier, handler):
        self.assertEqual(self.call()[0], "400 Bad Request")
        handler.assert_not_called()

    @patch.object(webhook, "lambda_handler")
    @patch.object(webhook, "verify_signature_and_timestamp")
    def test_missing_signature_bad_json_and_wrong_skill_fail_closed(self, verifier, handler):
        self.assertEqual(self.call(headers=False)[0], "400 Bad Request")
        self.assertEqual(self.call(body=b"not json")[0], "400 Bad Request")
        value = event()
        value["context"]["System"]["application"]["applicationId"] = "other"
        self.assertEqual(self.call(value=value)[0], "400 Bad Request")
        self.assertEqual(self.call(body=b"x" * 32769)[0], "400 Bad Request")
        verifier.assert_not_called()
        handler.assert_not_called()

    def test_health_is_separate_from_skill_enablement(self):
        status, body = self.call(method="GET", path="/health")
        self.assertEqual(status, "200 OK")
        self.assertTrue(body["skill_configured"])
        self.assertEqual(self.call(method="GET")[0], "405 Method Not Allowed")


if __name__ == "__main__":
    unittest.main()
