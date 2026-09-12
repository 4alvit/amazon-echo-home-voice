"""Bounded OAuth clients for Alexa linking and the household management portal.

The configured identity provider owns passwords, authorization codes, refresh
tokens, and account recovery. This module never decodes an unverified JWT or
implements an authorization server. Every identity comes from an authenticated
HTTPS introspection response, with separate clients for Alexa and the portal.
"""

import base64
from dataclasses import dataclass, field
import hashlib
from http.client import HTTPException
import json
import math
import os
from queue import Empty, Queue
import re
from threading import BoundedSemaphore, Thread
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


MAX_RESPONSE_BYTES = 16384
MAX_ACCESS_TOKEN_LENGTH = 8192
ALEXA_SCOPE = "energy:read"
PORTAL_SCOPE = "openid"
USER_AGENT = "HomeEnergyAccountLinking/1.0"
# A resolver cannot be interrupted portably. Bound abandoned work as well as the
# caller's latency; exhausted slots fail closed instead of creating more threads.
_REQUEST_SLOTS = BoundedSemaphore(8)


class OAuthError(Exception):
    """Safe error category without credentials, identities, or remote responses."""


class InvalidToken(OAuthError):
    """The caller must authenticate again; the supplied identity is not valid."""


class OAuthUnavailable(OAuthError):
    """Identity verification could not complete; do not request unnecessary relinking."""


def _visible_string(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and all(33 <= ord(char) <= 126 for char in value)
    )


def _finite_number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _https_url(value: object) -> bool:
    if not _visible_string(value, 2048) or "\\" in value:
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and not parsed.query
            and not parsed.fragment
            and "?" not in value
            and "#" not in value
            and "%" not in parsed.netloc
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class OAuthConfig:
    """Trusted operator configuration; never populated from a browser request."""

    issuer: str
    authorization_url: str
    token_url: str
    introspection_url: str
    alexa_client_id: str
    alexa_client_secret: str = field(repr=False)
    portal_client_id: str
    portal_client_secret: str = field(repr=False)
    portal_redirect_uri: str
    timeout_seconds: float = 2.0

    def __post_init__(self) -> None:
        if not all(_https_url(value) for value in (
            self.issuer, self.authorization_url, self.token_url,
            self.introspection_url, self.portal_redirect_uri,
        )):
            raise OAuthError("OAuth endpoints must use HTTPS on port 443 without queries or fragments")
        if not all(_visible_string(value, 255) for value in (
            self.alexa_client_id, self.portal_client_id,
        )) or self.alexa_client_id == self.portal_client_id:
            raise OAuthError("Configure distinct Alexa and portal client identifiers")
        if not all(_visible_string(value, 4096) for value in (
            self.alexa_client_secret, self.portal_client_secret,
        )):
            raise OAuthError("Configure both confidential client credentials")
        if (
            not _finite_number(self.timeout_seconds)
            or not 0.1 <= self.timeout_seconds <= 2.0
        ):
            raise OAuthError("OAuth timeout must be between 0.1 and 2 seconds")

    @classmethod
    def from_env(cls) -> "OAuthConfig":
        try:
            return cls(
                issuer=os.environ.get("OAUTH_ISSUER", ""),
                authorization_url=os.environ.get("OAUTH_AUTHORIZATION_URL", ""),
                token_url=os.environ.get("OAUTH_TOKEN_URL", ""),
                introspection_url=os.environ.get("OAUTH_INTROSPECTION_URL", ""),
                alexa_client_id=os.environ.get("OAUTH_ALEXA_CLIENT_ID", ""),
                alexa_client_secret=os.environ.get("OAUTH_ALEXA_CLIENT_SECRET", ""),
                portal_client_id=os.environ.get("OAUTH_PORTAL_CLIENT_ID", ""),
                portal_client_secret=os.environ.get("OAUTH_PORTAL_CLIENT_SECRET", ""),
                portal_redirect_uri=os.environ.get("OAUTH_PORTAL_REDIRECT_URI", ""),
                timeout_seconds=float(os.environ.get("OAUTH_TIMEOUT_SECONDS", "2")),
            )
        except ValueError:
            raise OAuthError("Invalid OAuth timeout configuration") from None


@dataclass(frozen=True)
class Identity:
    subject: str = field(repr=False)
    issuer: str = field(repr=False)
    expires_at: float


@dataclass(frozen=True)
class PortalLogin:
    identity: Identity
    access_token: str = field(repr=False)
    expires_at: float


class NoRedirects(HTTPRedirectHandler):
    """Do not send OAuth credentials to any redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise OAuthUnavailable("Identity provider returned an invalid response")
        result[key] = value
    return result


def _invalid_constant(value: str):
    raise OAuthUnavailable("Identity provider returned an invalid response")


def _read_json(response) -> dict:
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise OAuthUnavailable("Identity provider returned an invalid response")
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise OAuthUnavailable("Identity provider returned an invalid response")
    payload = json.loads(
        body.decode("utf-8"), object_pairs_hook=_unique_object,
        parse_constant=_invalid_constant,
    )
    if not isinstance(payload, dict):
        raise OAuthUnavailable("Identity provider returned an invalid response")
    return payload


class OAuthClient:
    def __init__(self, config: OAuthConfig, *, opener=None, clock=None):
        self.config = config
        self._opener = opener
        self._clock = clock or time.time

    def _post_json(
        self, url: str, fields: dict, client_id: str, client_secret: str,
        *, authorization_code: bool = False,
    ) -> dict:
        # RFC 6749 section 2.3.1 requires form encoding before HTTP Basic encoding.
        credentials = f"{quote_plus(client_id)}:{quote_plus(client_secret)}".encode("ascii")
        request = Request(
            url, data=urlencode(fields).encode("ascii"), method="POST",
            headers={
                "Authorization": "Basic " + base64.b64encode(credentials).decode("ascii"),
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "Cache-Control": "no-store",
                # Identify this OAuth client explicitly; identity-provider
                # gateways can reject the generic HTTP library user agent.
                "User-Agent": USER_AGENT,
            },
        )
        deadline = time.monotonic() + self.config.timeout_seconds
        if not _REQUEST_SLOTS.acquire(blocking=False):
            raise OAuthUnavailable("Identity provider is busy")
        result = Queue(maxsize=1)

        def run():
            try:
                value = self._request_json(request, authorization_code=authorization_code)
                result.put_nowait((True, value))
            except OAuthError as error:
                result.put_nowait((False, error))
            except Exception:
                # A library failure must not expose its URL, body, or credentials.
                result.put_nowait((False, OAuthUnavailable("Identity provider request failed")))
            finally:
                _REQUEST_SLOTS.release()

        try:
            Thread(target=run, name="oauth-request", daemon=True).start()
        except RuntimeError:
            _REQUEST_SLOTS.release()
            raise OAuthUnavailable("Identity provider is busy") from None
        try:
            success, value = result.get(timeout=max(0, deadline - time.monotonic()))
        except Empty:
            raise OAuthUnavailable("Identity provider request timed out") from None
        if time.monotonic() > deadline:
            raise OAuthUnavailable("Identity provider request timed out")
        if not success:
            raise value from None
        return value

    def _request_json(self, request: Request, *, authorization_code: bool) -> dict:
        # Each production request has its own opener. Neither shared handlers nor
        # environment proxy configuration can redirect authenticated traffic.
        opener = self._opener or build_opener(ProxyHandler({}), NoRedirects())
        try:
            with opener.open(request, timeout=self.config.timeout_seconds) as response:
                if response.status != 200:
                    raise OAuthUnavailable("Identity provider request failed")
                return _read_json(response)
        except HTTPError as error:
            try:
                if authorization_code and error.code == 400:
                    payload = _read_json(error)
                    if payload.get("error") == "invalid_grant":
                        raise InvalidToken("Sign in again") from None
            except (OAuthUnavailable, UnicodeError, ValueError, RecursionError, OSError, HTTPException):
                pass
            finally:
                error.close()
            raise OAuthUnavailable("Identity provider request failed") from None
        except (URLError, OSError, TimeoutError, HTTPException):
            raise OAuthUnavailable("Identity provider request failed") from None
        except (UnicodeError, ValueError, RecursionError):
            raise OAuthUnavailable("Identity provider returned an invalid response") from None

    def _introspect(self, access_token: object, client_id: str, client_secret: str, scope: str) -> Identity:
        if not _visible_string(access_token, MAX_ACCESS_TOKEN_LENGTH):
            raise InvalidToken("Account access is invalid")
        payload = self._post_json(
            self.config.introspection_url,
            {"token": access_token, "token_type_hint": "access_token"},
            client_id, client_secret,
        )
        audience = payload.get("aud")
        if isinstance(audience, str):
            audience = [audience]
        clients = [payload[name] for name in ("client_id", "azp") if name in payload]
        expires_at = payload.get("exp")
        token_scope = payload.get("scope")
        valid = (
            payload.get("active") is True
            and payload.get("iss") == self.config.issuer
            and isinstance(audience, list)
            and all(_visible_string(value, 255) for value in audience)
            and client_id in audience
            and bool(clients)
            and all(value == client_id for value in clients)
            and _visible_string(payload.get("sub"), 255)
            and _finite_number(expires_at)
            and expires_at > self._clock()
            and isinstance(token_scope, str)
            and len(token_scope) <= 4096
            and all(32 <= ord(char) <= 126 for char in token_scope)
            and scope in token_scope.split(" ")
        )
        if not valid:
            raise InvalidToken("Account access is invalid")
        if "nbf" in payload and (
            not _finite_number(payload["nbf"])
            or payload["nbf"] > self._clock()
        ):
            raise InvalidToken("Account access is invalid")
        return Identity(subject=payload["sub"], issuer=self.config.issuer, expires_at=expires_at)

    def introspect_alexa(self, access_token: object) -> Identity:
        return self._introspect(
            access_token, self.config.alexa_client_id,
            self.config.alexa_client_secret, ALEXA_SCOPE,
        )

    def introspect_portal(self, access_token: object) -> Identity:
        return self._introspect(
            access_token, self.config.portal_client_id,
            self.config.portal_client_secret, PORTAL_SCOPE,
        )

    @staticmethod
    def _validate_verifier(code_verifier: object) -> None:
        if not isinstance(code_verifier, str) or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", code_verifier):
            raise OAuthError("Invalid authorization transaction")

    def authorization_url(self, state: str, code_verifier: str) -> str:
        """Build the portal login URL; the caller stores and consumes state once."""
        if not isinstance(state, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", state):
            raise OAuthError("Invalid authorization transaction")
        self._validate_verifier(code_verifier)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest()).rstrip(b"=")
        return self.config.authorization_url + "?" + urlencode({
            "response_type": "code",
            "client_id": self.config.portal_client_id,
            "redirect_uri": self.config.portal_redirect_uri,
            "scope": PORTAL_SCOPE,
            "state": state,
            "code_challenge": challenge.decode("ascii"),
            "code_challenge_method": "S256",
        })

    def exchange_portal_code(self, code: str, code_verifier: str) -> PortalLogin:
        """Exchange the one-time code, then verify its access token independently."""
        if not _visible_string(code, 4096):
            raise InvalidToken("Sign in again")
        self._validate_verifier(code_verifier)
        payload = self._post_json(
            self.config.token_url,
            {
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": self.config.portal_redirect_uri,
                "code_verifier": code_verifier,
            },
            self.config.portal_client_id, self.config.portal_client_secret,
            authorization_code=True,
        )
        token = payload.get("access_token")
        if (
            not _visible_string(token, MAX_ACCESS_TOKEN_LENGTH)
            or not isinstance(payload.get("token_type"), str)
            or payload["token_type"].lower() != "bearer"
        ):
            raise OAuthUnavailable("Identity provider returned an invalid response")
        identity = self.introspect_portal(token)
        return PortalLogin(identity=identity, access_token=token, expires_at=identity.expires_at)
