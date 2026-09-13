"""Exercise real WSGI routes, encrypted storage, browser binding and household ownership."""

from dataclasses import replace
import io
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

from cryptography.fernet import Fernet

from amazon_echo_home_voice import portal
from amazon_echo_home_voice.oauth import Identity, PortalLogin, InvalidToken, OAuthUnavailable
from amazon_echo_home_voice.gateway import GatewayError
from amazon_echo_home_voice.tenant_store import TenantStore
from test_oauth import config


class PortalTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = str(Path(directory.name) / "households.sqlite3")
        self.key = Fernet.generate_key().decode()
        self.store = TenantStore(self.path, self.key, initialize=True)
        self.env = patch.dict(os.environ, {"ENERGY_VOICE_MODE": "multi_household", "TENANT_DB_PATH": self.path,
                                          "TENANT_ENCRYPTION_KEY": self.key}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config_patch = patch.object(portal.OAuthConfig, "from_env", return_value=config())
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        self.identity = Identity("first-home", config().issuer, time.time() + 300)
        self.introspect = patch.object(portal.OAuthClient, "introspect_portal", return_value=self.identity)
        self.auth = self.introspect.start()
        self.addCleanup(self.introspect.stop)
        self.token = self.store.create_session(self.identity.issuer, self.identity.subject, "synthetic-portal-token", self.identity.expires_at)
        self.cookie = f"{portal.SESSION_COOKIE}={self.token}"
        self.csrf = self.store.session(self.token)["csrf"]
        self.fields = {"csrf": self.csrf, "url": "https://first.example.com/v1/energy", "read_token": "synthetic-first-read-token",
                       "cf_client_id": "synthetic-cf-id", "cf_client_secret": "synthetic-cf-secret"}

    def call(self, path="/", method="GET", fields=None, *, cookie=None, query="", overrides=None):
        body = urlencode(fields or {}).encode()
        environ = {"PATH_INFO": path, "REQUEST_METHOD": method, "QUERY_STRING": query, "HTTP_HOST": "portal.example",
                   "HTTP_ORIGIN": "https://portal.example", "HTTP_COOKIE": self.cookie if cookie is None else cookie,
                   "CONTENT_TYPE": "application/x-www-form-urlencoded", "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
        environ.update(overrides or {})
        start = Mock()
        response = b"".join(portal.application(environ, start)).decode()
        status, headers = start.call_args.args
        return status, headers, response

    def test_two_households_save_and_read_only_their_own_connections(self):
        with patch.object(portal, "fetch_energy") as fetch:
            self.assertEqual(self.call("/connection", "POST", self.fields)[0], "303 See Other")
            self.assertTrue(fetch.call_args.args[0].public_only)
            second = replace(self.identity, subject="second-home")
            token = self.store.create_session(second.issuer, second.subject, "second-access-token", second.expires_at)
            fields = self.fields | {"csrf": self.store.session(token)["csrf"], "url": "https://second.example.com/v1/energy", "read_token": "synthetic-second-read-token"}
            self.auth.return_value = second
            self.assertEqual(self.call("/connection", "POST", fields, cookie=f"{portal.SESSION_COOKIE}={token}")[0], "303 See Other")
        first = self.store.connection(self.identity.issuer, self.identity.subject)
        other = self.store.connection(second.issuer, second.subject)
        self.assertEqual(first.read_token, self.fields["read_token"])
        self.assertEqual(other.read_token, "synthetic-second-read-token")
        self.auth.return_value = self.identity
        status, _, page = self.call()
        self.assertEqual(status, "200 OK")
        self.assertIn("Your gateway is connected", page)
        for secret in (first.url, first.read_token, first.cf_client_id, first.cf_client_secret, self.token, other.read_token):
            self.assertNotIn(secret, page)

    def test_missing_session_invalid_token_and_identity_swap_never_fetch(self):
        with patch.object(portal, "fetch_energy") as fetch:
            self.assertEqual(self.call("/connection", "POST", self.fields, cookie="")[0], "401 Unauthorized")
            self.auth.return_value = replace(self.identity, subject="different-home")
            self.assertEqual(self.call("/connection", "POST", self.fields)[0], "401 Unauthorized")
            self.assertIsNone(self.store.session(self.token))
            fetch.assert_not_called()

    def test_csrf_origin_extra_fields_and_duplicate_parameters_fail_before_gateway(self):
        cases = [({"csrf": "wrong"}, None), ({"csrf": "é"}, None), ({"owner": "another-home"}, None),
                 ({}, {"HTTP_ORIGIN": "https://evil.example"}), ({}, {"HTTP_ORIGIN": "null"}),
                 ({}, {"HTTP_ORIGIN": ""}), ({}, {"CONTENT_TYPE": "text/plain"})]
        with patch.object(portal, "fetch_energy") as fetch:
            for fields, overrides in cases:
                with self.subTest(fields=fields, overrides=overrides):
                    self.assertEqual(self.call("/connection", "POST", self.fields | fields, overrides=overrides)[0], "400 Bad Request")
            raw = b"csrf=one&csrf=two"
            self.assertEqual(self.call("/connection", "POST", overrides={"wsgi.input": io.BytesIO(raw), "CONTENT_LENGTH": str(len(raw))})[0], "400 Bad Request")
            fetch.assert_not_called()

    def test_failed_gateway_check_keeps_existing_connection_and_never_echoes_inputs(self):
        with patch.object(portal, "fetch_energy"):
            self.call("/connection", "POST", self.fields)
        with patch.object(portal, "fetch_energy", side_effect=GatewayError("sensitive-remote-response")):
            status, _, page = self.call("/connection", "POST", self.fields | {"read_token": "replacement-secret"})
        self.assertEqual(status, "400 Bad Request")
        self.assertNotIn("replacement-secret", page)
        self.assertNotIn("sensitive-remote-response", page)
        self.assertEqual(self.store.connection(self.identity.issuer, self.identity.subject).read_token, self.fields["read_token"])

    def test_erasure_or_logout_during_gateway_check_cannot_resurrect_connection(self):
        for erase in (lambda: self.store.delete_household(self.identity.issuer, self.identity.subject),
                      lambda: self.store.delete_session(self.token)):
            self.token = self.store.create_session(self.identity.issuer, self.identity.subject, "new-portal-token", self.identity.expires_at)
            self.cookie = f"{portal.SESSION_COOKIE}={self.token}"
            self.fields["csrf"] = self.store.session(self.token)["csrf"]
            with patch.object(portal, "fetch_energy", side_effect=lambda _: erase()):
                self.assertEqual(self.call("/connection", "POST", self.fields)[0], "503 Service Unavailable")
            self.assertIsNone(self.store.connection(self.identity.issuer, self.identity.subject))

    def test_disconnect_and_logout_require_post_csrf_and_affect_only_current_account(self):
        with patch.object(portal, "fetch_energy"):
            self.call("/connection", "POST", self.fields)
        config_first = self.store.connection(self.identity.issuer, self.identity.subject)
        self.store.save_connection(self.identity.issuer, "other", config_first)
        self.assertEqual(self.call("/disconnect")[0], "405 Method Not Allowed")
        self.assertEqual(self.call("/disconnect", "POST", {"csrf": self.csrf})[0], "303 See Other")
        self.assertIsNone(self.store.connection(self.identity.issuer, self.identity.subject))
        self.assertIsNotNone(self.store.connection(self.identity.issuer, "other"))
        self.assertEqual(self.call("/logout", "POST", {"csrf": self.csrf})[0], "303 See Other")
        self.assertIsNone(self.store.session(self.token))

    def test_login_callback_requires_browser_bound_state_and_rotates_session(self):
        status, headers, _ = self.call("/login")
        self.assertEqual(status, "303 See Other")
        location = dict(headers)["Location"]
        params = parse_qs(urlsplit(location).query)
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertEqual(params["redirect_uri"], [config().portal_redirect_uri])
        browser_cookie = next(value.split(";", 1)[0] for name, value in headers if name == "Set-Cookie")
        query = urlencode({"state": params["state"][0], "code": "synthetic-authorization-code", "iss": config().issuer})
        with patch.object(portal.OAuthClient, "exchange_portal_code", return_value=PortalLogin(self.identity, "new-access-token", self.identity.expires_at)) as exchange:
            self.assertEqual(self.call("/callback", query=query, cookie="")[0], "400 Bad Request")
            exchange.assert_not_called()
            status, headers, body = self.call("/callback", query=query, cookie=self.cookie + "; " + browser_cookie)
            self.assertEqual(status, "303 See Other")
            exchange.assert_called_once()
            self.assertIsNone(self.store.session(self.token))
            self.assertNotIn("new-access-token", str(headers) + body)
            new_cookie = next(value for name, value in headers if name == "Set-Cookie" and value.startswith(portal.SESSION_COOKIE))
            for flag in ("Secure", "HttpOnly", "SameSite=Lax", "Path=/"):
                self.assertIn(flag, new_cookie)
            self.assertNotIn(self.token, new_cookie)
            self.assertEqual(self.call("/callback", query=query, cookie=browser_cookie)[0], "400 Bad Request")
            self.assertEqual(exchange.call_count, 1)

    def test_callback_wrong_issuer_and_provider_error_cannot_create_session(self):
        _, headers, _ = self.call("/login")
        state = parse_qs(urlsplit(dict(headers)["Location"]).query)["state"][0]
        browser_cookie = next(value.split(";", 1)[0] for name, value in headers if name == "Set-Cookie")
        with patch.object(portal.OAuthClient, "exchange_portal_code") as exchange:
            status, _, _ = self.call("/callback", query=urlencode({"state": state, "code": "code", "iss": "https://wrong.example"}), cookie=browser_cookie)
            self.assertEqual(status, "400 Bad Request")
            exchange.assert_not_called()

    def test_routes_headers_safe_outages_and_personal_mode_are_separate(self):
        self.assertEqual(self.call("/alexa")[0], "404 Not Found")
        self.assertEqual(self.call(overrides={"HTTP_HOST": "attacker.example"})[0], "400 Bad Request")
        self.assertEqual(self.call(query="owner=another-home")[0], "400 Bad Request")
        status, headers, _ = self.call()
        self.assertEqual(status, "200 OK")
        headers = dict(headers)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.auth.side_effect = OAuthUnavailable("private-token")
        status, _, body = self.call()
        self.assertEqual(status, "503 Service Unavailable")
        self.assertNotIn("private-token", body)
        with patch.dict(os.environ, {"ENERGY_VOICE_MODE": "personal"}):
            self.assertEqual(self.call()[0], "404 Not Found")


if __name__ == "__main__":
    unittest.main()
