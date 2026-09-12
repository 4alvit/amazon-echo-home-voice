"""Voice identity and gateway work share the time left after signature verification."""

import io
import json
import os
import unittest
from unittest.mock import Mock, patch

from amazon_echo_home_voice import accounts, lambda_handler, webhook
from amazon_echo_home_voice.gateway import GatewayConfig, UNAVAILABLE_TEXT
from amazon_echo_home_voice.oauth import Identity
from test_oauth import config as oauth_config
from test_skill import event, payload, SKILL_ID


@patch.dict(os.environ, {"ASK_SKILL_ID": SKILL_ID, "ENERGY_VOICE_MODE": "multi_household"}, clear=True)
class RequestBudgetTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.clock = patch.object(accounts.time, "monotonic", side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.connection = GatewayConfig("https://home.example/v1/energy", "synthetic-token", public_only=True)
        self.identity = Identity("synthetic-subject", oauth_config().issuer, 9999999999)

    def call(self, *, verification_seconds=0, verification_error=None):
        value = event()
        value["context"]["System"]["user"] = {"accessToken": "synthetic-access-token"}
        body = json.dumps(value).encode()
        environ = {
            "PATH_INFO": "/alexa", "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": "application/json", "CONTENT_LENGTH": str(len(body)),
            "HTTP_SIGNATURECERTCHAINURL": "https://s3.amazonaws.com/echo.api/example.pem",
            "HTTP_SIGNATURE_256": "synthetic-signature", "wsgi.input": io.BytesIO(body),
        }

        def verify(*args):
            self.now += verification_seconds
            if verification_error is not None:
                raise verification_error

        start = Mock()
        with patch.object(webhook, "verify_signature_and_timestamp", side_effect=verify) as verifier:
            response = json.loads(b"".join(webhook.application(environ, start)))
        verifier.assert_called_once()
        return start.call_args.args[0], response

    def test_crypto_identity_and_storage_time_reduce_outbound_budgets(self):
        store = Mock()

        def introspect(token):
            self.now += 1.2
            return self.identity

        def load(issuer, subject):
            self.now += 0.3
            return self.connection

        store.connection.side_effect = load
        with patch.object(accounts.OAuthConfig, "from_env", return_value=oauth_config()), \
                patch.object(accounts, "OAuthClient") as client, \
                patch.object(accounts.TenantStore, "from_env", return_value=store), \
                patch.object(lambda_handler, "fetch_energy", return_value=payload()) as fetch:
            client.return_value.introspect_alexa.side_effect = introspect
            status, response = self.call(verification_seconds=2.5)
        self.assertEqual(status, "200 OK")
        self.assertEqual(response["response"]["outputSpeech"]["text"], "Central battery report.")
        self.assertAlmostEqual(client.call_args.args[0].timeout_seconds, 1.75)
        self.assertAlmostEqual(fetch.call_args.args[0].timeout_seconds, 0.9)
        self.assertTrue(fetch.call_args.args[0].public_only)
        self.assertEqual(fetch.call_args.args[0].read_token, "synthetic-token")
        store.connection.assert_called_once_with(self.identity.issuer, self.identity.subject)

    def test_verification_that_uses_the_budget_prevents_identity_or_gateway_work(self):
        with patch.object(accounts, "OAuthClient") as client, \
                patch.object(accounts.TenantStore, "from_env") as store, \
                patch.object(lambda_handler, "fetch_energy") as fetch:
            status, response = self.call(verification_seconds=5.01)
        self.assertEqual(status, "200 OK")
        self.assertEqual(response["response"]["outputSpeech"]["text"], UNAVAILABLE_TEXT)
        client.assert_not_called()
        store.assert_not_called()
        fetch.assert_not_called()

    def test_expensive_storage_prevents_starting_a_gateway_request_after_deadline(self):
        store = Mock()

        def load(issuer, subject):
            self.now += 4.0
            return self.connection

        store.connection.side_effect = load
        with patch.object(accounts.OAuthConfig, "from_env", return_value=oauth_config()), \
                patch.object(accounts.OAuthClient, "introspect_alexa", return_value=self.identity), \
                patch.object(accounts.TenantStore, "from_env", return_value=store), \
                patch.object(lambda_handler, "fetch_energy") as fetch:
            status, response = self.call(verification_seconds=1.2)
        self.assertEqual(status, "200 OK")
        self.assertEqual(response["response"]["outputSpeech"]["text"], UNAVAILABLE_TEXT)
        fetch.assert_not_called()

    def test_identity_that_finishes_late_does_not_start_storage_work(self):
        def introspect(token):
            self.now += 5.0
            return self.identity

        with patch.object(accounts.OAuthConfig, "from_env", return_value=oauth_config()), \
                patch.object(accounts.OAuthClient, "introspect_alexa", side_effect=introspect), \
                patch.object(accounts.TenantStore, "from_env") as store, \
                patch.object(lambda_handler, "fetch_energy") as fetch:
            status, response = self.call(verification_seconds=0.2)
        self.assertEqual(status, "200 OK")
        self.assertEqual(response["response"]["outputSpeech"]["text"], UNAVAILABLE_TEXT)
        store.assert_not_called()
        fetch.assert_not_called()

    def test_late_gateway_result_is_not_returned_as_current_speech(self):
        def fetch(config):
            self.now += 5.0
            return payload()

        with patch.object(lambda_handler, "household_connection", return_value=self.connection), \
                patch.object(lambda_handler, "fetch_energy", side_effect=fetch):
            status, response = self.call(verification_seconds=0.2)
        self.assertEqual(status, "200 OK")
        self.assertEqual(response["response"]["outputSpeech"]["text"], UNAVAILABLE_TEXT)

    def test_time_budget_never_turns_an_invalid_signature_into_a_success_response(self):
        with patch.object(webhook, "lambda_handler") as handler:
            status, response = self.call(verification_seconds=5.01, verification_error=ValueError("invalid signature"))
        self.assertEqual(status, "400 Bad Request")
        self.assertEqual(response, {"error": "Invalid Alexa request"})
        handler.assert_not_called()

    def test_direct_personal_lambda_keeps_its_existing_timeout_without_a_deadline(self):
        with patch.dict(os.environ, {"ENERGY_VOICE_MODE": "personal", "IGW_URL": "https://home.example/v1/energy",
                                    "IGW_READ_TOKEN": "synthetic-token", "IGW_TIMEOUT_SECONDS": "4"}), \
                patch.object(lambda_handler, "fetch_energy", return_value=payload()) as fetch:
            result = lambda_handler.lambda_handler(event(), None)
        self.assertEqual(result["response"]["outputSpeech"]["text"], "Central battery report.")
        self.assertEqual(fetch.call_args.args[0].timeout_seconds, 4)
        self.assertFalse(fetch.call_args.args[0].public_only)


if __name__ == "__main__":
    unittest.main()
