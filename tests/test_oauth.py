import base64
from dataclasses import replace
from http.client import IncompleteRead
import io
import json
import os
import socket
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from amazon_echo_home_voice import oauth


NOW = 1800000000
VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
STATE = "synthetic-authorization-transaction-state"


def config():
    return oauth.OAuthConfig(
        issuer="https://identity.example/realms/energy",
        authorization_url="https://identity.example/realms/energy/protocol/openid-connect/auth",
        token_url="https://identity.example/realms/energy/protocol/openid-connect/token",
        introspection_url="https://identity.example/realms/energy/protocol/openid-connect/token/introspect",
        alexa_client_id="energy-alexa", alexa_client_secret="synthetic-alexa-secret",
        portal_client_id="energy-portal", portal_client_secret="synthetic-portal-secret",
        portal_redirect_uri="https://portal.example/callback",
    )


def claims(client="energy-alexa", subject="household-a"):
    return {
        "active": True, "iss": config().issuer, "sub": subject,
        "aud": [client], "client_id": client, "azp": client,
        "exp": NOW + 300,
        "scope": "openid energy:read" if client == "energy-alexa" else "openid",
    }


def response(body=None, *, status=200, content_type="application/json"):
    result = Mock()
    result.status = status
    result.headers = {"Content-Type": content_type}
    result.read.return_value = json.dumps(claims()).encode() if body is None else body
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


def client(*responses):
    opener = Mock()
    opener.open.side_effect = list(responses or [response()])
    return oauth.OAuthClient(config(), opener=opener, clock=lambda: NOW), opener


class OAuthTests(unittest.TestCase):
    def test_introspection_uses_its_confidential_client_and_returns_only_validated_identity(self):
        instance, opener = client()
        identity = instance.introspect_alexa("synthetic-access-token")
        self.assertEqual(identity.subject, "household-a")
        self.assertEqual(identity.issuer, config().issuer)
        self.assertEqual(identity.expires_at, NOW + 300)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, config().introspection_url)
        self.assertEqual(request.method, "POST")
        self.assertEqual(parse_qs(request.data.decode()), {
            "token": ["synthetic-access-token"], "token_type_hint": ["access_token"],
        })
        self.assertEqual(base64.b64decode(request.get_header("Authorization")[6:]).decode(),
                         "energy-alexa:synthetic-alexa-secret")
        opener.open.assert_called_once_with(request, timeout=2.0)
        opener.open.side_effect = [response()]
        with self.assertRaises(oauth.InvalidToken):
            instance.introspect_portal("synthetic-access-token")
        request = opener.open.call_args.args[0]
        self.assertEqual(base64.b64decode(request.get_header("Authorization")[6:]).decode(),
                         "energy-portal:synthetic-portal-secret")

    def test_households_remain_distinct_and_no_positive_token_cache_hides_revocation(self):
        instance, opener = client(
            response(json.dumps(claims(subject="household-a")).encode()),
            response(json.dumps(claims(subject="household-b")).encode()),
            response(b'{"active":false}'),
        )
        self.assertNotEqual(instance.introspect_alexa("token-a").subject,
                            instance.introspect_alexa("token-b").subject)
        with self.assertRaises(oauth.InvalidToken):
            instance.introspect_alexa("token-a")
        self.assertEqual(opener.open.call_count, 3)

    def test_claim_validation_fails_closed(self):
        for change in (
            {"active": "true"}, {"active": 1}, {"active": False},
            {"iss": "https://other.example/realms/energy"}, {"iss": config().issuer + "/"},
            {"sub": ""}, {"sub": None}, {"sub": 1}, {"sub": "a" * 256}, {"sub": "injected\nsubject"},
            {"exp": NOW}, {"exp": NOW - 1}, {"exp": True}, {"exp": "1800000300"},
            {"exp": None}, {"exp": 10 ** 400}, {"nbf": NOW + 1}, {"nbf": "later"}, {"nbf": 10 ** 400},
            {"aud": []}, {"aud": ["energy-portal"]}, {"aud": ["energy-alexa", None]}, {"aud": {}},
            {"client_id": "energy-portal"}, {"azp": "energy-portal"}, {"client_id": None},
            {"scope": "openid"}, {"scope": "not-energy:read"}, {"scope": ["energy:read"]},
            {"scope": "openid\nenergy:read"},
        ):
            instance, _ = client(response(json.dumps(claims() | change).encode()))
            with self.subTest(change=change), self.assertRaises(oauth.InvalidToken):
                instance.introspect_alexa("synthetic-access-token")
        for omitted in ("active", "iss", "sub", "exp", "aud", "scope"):
            value = claims()
            del value[omitted]
            instance, _ = client(response(json.dumps(value).encode()))
            with self.subTest(omitted=omitted), self.assertRaises(oauth.InvalidToken):
                instance.introspect_alexa("synthetic-access-token")
        value = claims()
        del value["client_id"]
        del value["azp"]
        instance, _ = client(response(json.dumps(value).encode()))
        with self.assertRaises(oauth.InvalidToken):
            instance.introspect_alexa("synthetic-access-token")

    def test_string_audience_and_single_authorized_party_are_supported(self):
        for omitted in ("client_id", "azp"):
            value = claims() | {"aud": "energy-alexa", "nbf": NOW - 1}
            del value[omitted]
            instance, _ = client(response(json.dumps(value).encode()))
            self.assertEqual(instance.introspect_alexa("token").subject, "household-a")

    def test_missing_or_oversized_tokens_fail_before_network(self):
        instance, opener = client()
        for token in (None, "", 42, "a b", "a\r\nInjected:1", "é", "x" * (oauth.MAX_ACCESS_TOKEN_LENGTH + 1)):
            with self.subTest(token_type=type(token)), self.assertRaises(oauth.InvalidToken):
                instance.introspect_alexa(token)
        opener.open.assert_not_called()

    def test_malformed_and_unbounded_provider_responses_are_outages(self):
        for result in (
            response(status=503), response(content_type="text/html"), response(b"not json"),
            response(b"[]"), response(b"\xff"), response(b'{"active":true,"active":false}'),
            response(b'{"exp":NaN}'), response(b'{"exp":Infinity}'),
            response(b"x" * (oauth.MAX_RESPONSE_BYTES + 1)),
        ):
            instance, _ = client(result)
            with self.subTest(result=result), self.assertRaises(oauth.OAuthUnavailable):
                instance.introspect_alexa("synthetic-access-token")
        good = response()
        instance, _ = client(good)
        instance.introspect_alexa("synthetic-access-token")
        good.read.assert_called_once_with(oauth.MAX_RESPONSE_BYTES + 1)

    def test_provider_errors_do_not_expose_secrets_or_demand_relinking(self):
        for error in (
            URLError("synthetic-sensitive-body"), OSError("synthetic-sensitive-body"), TimeoutError(),
            IncompleteRead(b"synthetic-sensitive-body"),
            HTTPError("https://identity.example/private", 401, "synthetic-sensitive-body", {}, None),
            HTTPError("https://identity.example/private", 302, "synthetic-sensitive-body", {}, None),
        ):
            instance, opener = client()
            opener.open.side_effect = error
            with self.subTest(error_type=type(error)), self.assertRaises(oauth.OAuthUnavailable) as caught:
                instance.introspect_alexa("synthetic-sensitive-token")
            self.assertNotIn("synthetic-sensitive", str(caught.exception))
            self.assertTrue(caught.exception.__suppress_context__)

    def test_redirects_and_environment_proxy_are_disabled(self):
        request = Request(config().introspection_url, headers={"Authorization": "Basic private"})
        for target in (config().token_url, "https://other.example", "http://identity.example"):
            self.assertIsNone(oauth.NoRedirects().redirect_request(request, None, 302, "", {}, target))
        with patch.object(oauth, "build_opener") as build, patch.object(oauth, "ProxyHandler") as proxy:
            build.return_value.open.return_value = response()
            oauth.OAuthClient(config(), clock=lambda: NOW).introspect_alexa("token")
        proxy.assert_called_once_with({})
        self.assertIsInstance(build.call_args.args[1], oauth.NoRedirects)

    def test_slow_dns_and_response_body_obey_the_total_deadline(self):
        for stage in ("dns", "body"):
            released = threading.Event()
            entered = threading.Event()
            slots = threading.BoundedSemaphore(1)

            def slow_dns(*args, **kwargs):
                entered.set()
                released.wait(3)
                raise OSError("Synthetic resolver failure")

            def slow_read(*args, **kwargs):
                entered.set()
                released.wait(3)
                return json.dumps(claims()).encode()

            stalled_response = response()
            stalled_response.read.side_effect = slow_read
            opener = Mock()
            opener.open.return_value = stalled_response
            # Load the TLS trust store before measuring the stalled DNS request.
            # Cold certificate loading can exceed this intentionally short deadline.
            dns_opener = oauth.build_opener(oauth.ProxyHandler({}), oauth.NoRedirects())
            instance = oauth.OAuthClient(
                replace(config(), timeout_seconds=0.1),
                opener=opener if stage == "body" else dns_opener, clock=lambda: NOW,
            )
            with self.subTest(stage=stage), patch.object(oauth, "_REQUEST_SLOTS", slots), patch.object(socket, "getaddrinfo", slow_dns):
                try:
                    started = time.monotonic()
                    with self.assertRaises(oauth.OAuthUnavailable):
                        instance.introspect_alexa("token")
                    self.assertLess(time.monotonic() - started, 1.0)
                    self.assertTrue(entered.is_set())
                    self.assertFalse(slots.acquire(blocking=False))
                    with self.assertRaises(oauth.OAuthUnavailable):
                        instance.introspect_alexa("another-token")
                    if stage == "body":
                        self.assertEqual(opener.open.call_count, 1)
                finally:
                    released.set()
                    self.assertTrue(slots.acquire(timeout=2))
                    slots.release()

    def test_portal_authorization_uses_pkce_and_fixed_callback(self):
        instance, opener = client()
        result = instance.authorization_url(STATE, VERIFIER)
        self.assertEqual(result.split("?", 1)[0], config().authorization_url)
        self.assertEqual(parse_qs(urlsplit(result).query), {
            "response_type": ["code"], "client_id": ["energy-portal"],
            "redirect_uri": ["https://portal.example/callback"], "scope": ["openid"],
            "state": [STATE], "code_challenge_method": ["S256"],
            "code_challenge": ["E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"],
        })
        self.assertNotIn(VERIFIER, result)
        self.assertNotIn(config().portal_client_secret, result)
        opener.open.assert_not_called()
        for state, verifier in (("short", VERIFIER), (STATE, "short"), (STATE, "a" * 129), (STATE, "x" * 42 + "!")):
            with self.assertRaises(oauth.OAuthError):
                instance.authorization_url(state, verifier)

    def test_portal_exchange_authenticates_token_with_separate_client(self):
        instance, opener = client(
            response(b'{"access_token":"synthetic-portal-token","token_type":"Bearer","refresh_token":"ignored"}'),
            response(json.dumps(claims("energy-portal")).encode()),
        )
        login = instance.exchange_portal_code("synthetic-code", VERIFIER)
        self.assertEqual(login.identity.subject, "household-a")
        self.assertEqual(login.access_token, "synthetic-portal-token")
        self.assertEqual(login.expires_at, NOW + 300)
        self.assertNotIn(login.access_token, repr(login))
        self.assertNotIn("household-a", repr(login))
        requests = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual(requests[0].full_url, config().token_url)
        self.assertEqual(parse_qs(requests[0].data.decode()), {
            "grant_type": ["authorization_code"], "code": ["synthetic-code"],
            "redirect_uri": [config().portal_redirect_uri], "code_verifier": [VERIFIER],
        })
        self.assertEqual(parse_qs(requests[1].data.decode())["token"], ["synthetic-portal-token"])
        for request in requests:
            self.assertEqual(base64.b64decode(request.get_header("Authorization")[6:]).decode(),
                             "energy-portal:synthetic-portal-secret")

    def test_portal_never_accepts_an_alexa_token_or_unverified_id_token(self):
        instance, _ = client(
            response(b'{"access_token":"alexa-token","token_type":"bearer","id_token":"unverified"}'),
            response(json.dumps(claims()).encode()),
        )
        with self.assertRaises(oauth.InvalidToken):
            instance.exchange_portal_code("synthetic-code", VERIFIER)
        for payload in (
            {"id_token": "unverified"}, {"access_token": "token"},
            {"access_token": "token", "token_type": "MAC"},
        ):
            instance, opener = client(response(json.dumps(payload).encode()))
            with self.assertRaises(oauth.OAuthUnavailable):
                instance.exchange_portal_code("synthetic-code", VERIFIER)
            self.assertEqual(opener.open.call_count, 1)

    def test_used_authorization_code_requires_login_but_provider_failures_remain_outages(self):
        for error_code, expected in (("invalid_grant", oauth.InvalidToken), ("invalid_client", oauth.OAuthUnavailable)):
            error = HTTPError(config().token_url, 400, "sensitive-detail", {"Content-Type": "application/json"},
                              io.BytesIO(json.dumps({"error": error_code, "error_description": "sensitive-detail"}).encode()))
            instance, opener = client()
            opener.open.side_effect = error
            with self.assertRaises(expected) as caught:
                instance.exchange_portal_code("synthetic-code", VERIFIER)
            self.assertNotIn("sensitive-detail", str(caught.exception))
            self.assertTrue(error.closed)
        error = HTTPError(config().token_url, 400, "sensitive-detail", {"Content-Type": "application/json"},
                          io.BytesIO(b'{"error":"invalid_grant","error":"duplicate"}'))
        instance, opener = client()
        opener.open.side_effect = error
        with self.assertRaises(oauth.OAuthUnavailable) as caught:
            instance.exchange_portal_code("synthetic-code", VERIFIER)
        self.assertTrue(caught.exception.__suppress_context__)

    def test_config_rejects_unsafe_endpoints_and_client_confusion(self):
        for field in ("issuer", "authorization_url", "token_url", "introspection_url", "portal_redirect_uri"):
            for value in (
                "http://identity.example/path", "https://user:secret@identity.example/path",
                "https://identity.example:8443/path", "https://identity.example/path?query=1",
                "https://identity.example/path#fragment", "https://identity.example/path?",
                "https://identity.example/path#", "https://identity.example/path\n",
                "https://identity.example\\other.example/path", "https://identity.example%2Fother.example/path",
            ):
                with self.subTest(field=field, value=value), self.assertRaises(oauth.OAuthError):
                    replace(config(), **{field: value})
        for change in (
            {"alexa_client_id": "energy-portal"}, {"portal_client_secret": ""},
            {"alexa_client_secret": "injected\nvalue"}, {"portal_client_id": ""},
            {"timeout_seconds": 2.1}, {"timeout_seconds": 0},
            {"timeout_seconds": float("nan")}, {"timeout_seconds": True}, {"timeout_seconds": 10 ** 400},
        ):
            with self.subTest(change=change), self.assertRaises(oauth.OAuthError):
                replace(config(), **change)
        self.assertNotIn("synthetic-alexa-secret", repr(config()))
        self.assertNotIn("synthetic-portal-secret", repr(config()))

    def test_environment_loading_is_explicit_and_validated(self):
        environment = {
            "OAUTH_ISSUER": config().issuer,
            "OAUTH_AUTHORIZATION_URL": config().authorization_url,
            "OAUTH_TOKEN_URL": config().token_url,
            "OAUTH_INTROSPECTION_URL": config().introspection_url,
            "OAUTH_ALEXA_CLIENT_ID": config().alexa_client_id,
            "OAUTH_ALEXA_CLIENT_SECRET": config().alexa_client_secret,
            "OAUTH_PORTAL_CLIENT_ID": config().portal_client_id,
            "OAUTH_PORTAL_CLIENT_SECRET": config().portal_client_secret,
            "OAUTH_PORTAL_REDIRECT_URI": config().portal_redirect_uri,
        }
        with patch.dict(os.environ, environment, clear=True):
            self.assertEqual(oauth.OAuthConfig.from_env(), config())
        with patch.dict(os.environ, environment | {"OAUTH_TIMEOUT_SECONDS": "invalid"}, clear=True):
            with self.assertRaises(oauth.OAuthError) as caught:
                oauth.OAuthConfig.from_env()
            self.assertNotIn("invalid", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
