#!/usr/bin/env python3
"""Exercise real Keycloak OAuth flows in disposable local Docker containers.

Requires Docker, openssl, Python 3.11+, and PYTHONPATH=src. This is an opt-in
integration test, not part of ordinary mocked test discovery. All identities
and credentials are synthetic and exist only in an owner-only temporary folder.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import replace
import hashlib
from html.parser import HTMLParser
from http.client import HTTPConnection, HTTPSConnection
from http.cookiejar import CookieJar
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import tempfile
from threading import Thread
import time
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import HTTPSHandler, HTTPCookieProcessor, ProxyHandler, Request, build_opener

from amazon_echo_home_voice.oauth import InvalidToken, OAuthClient, OAuthConfig, OAuthError, NoRedirects


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("generate_realm", ROOT / "deploy/multi-household/generate_realm.py")
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)
ISSUER = "https://auth.example.test/realms/home-energy"
ALEXA_REDIRECT = "https://alexa.example.test/exact-callback"
PORTAL_REDIRECT = "https://connect.example.test/callback"


class AuthorizationRejected(Exception):
    """The real provider rejected an invalid authorization request."""


class CheckedOAuthClient(OAuthClient):
    """Report rejected claim names only when diagnosing an integration failure."""

    rejected_claims = ()

    def _introspect(self, access_token, client_id, client_secret, scope):
        try:
            return super()._introspect(access_token, client_id, client_secret, scope)
        except InvalidToken:
            payload = self._post_json(self.config.introspection_url,
                                      {"token": access_token, "token_type_hint": "access_token"},
                                      client_id, client_secret)
            audience = payload.get("aud", [])
            if isinstance(audience, str):
                audience = [audience]
            checks = {
                "active": payload.get("active") is True,
                "iss": payload.get("iss") == self.config.issuer,
                "aud": isinstance(audience, list) and client_id in audience,
                "client_id_or_azp": any(payload.get(key) == client_id for key in ("client_id", "azp")),
                "sub": isinstance(payload.get("sub"), str) and bool(payload["sub"]),
                "exp": isinstance(payload.get("exp"), (int, float)) and payload["exp"] > time.time(),
                "scope": scope in payload.get("scope", "").split(),
                "nbf": payload.get("nbf", 0) <= time.time(),
            }
            self.rejected_claims = tuple(key for key, valid in checks.items() if not valid)
            raise


def checked(client, description, operation):
    client.rejected_claims = ()
    try:
        return operation()
    except OAuthError:
        fields = ", ".join(client.rejected_claims) or "transport or token exchange"
        raise RuntimeError(f"{description} failed validation: {fields}.") from None


def docker(*arguments: str) -> str:
    result = subprocess.run(["docker", *arguments], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode:
        raise RuntimeError(f"A disposable Docker {arguments[0]} command failed.")
    return result.stdout.strip()


def private_text(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8") as output:
        os.chmod(path, 0o600)
        output.write(text)


class FormParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = []
        self.current = None

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "form":
            self.current = {"action": attributes.get("action", ""), "fields": {}, "password": False}
            self.forms.append(self.current)
        elif self.current is not None and tag == "input":
            if attributes.get("name"):
                self.current["fields"][attributes["name"]] = attributes.get("value", "")
            if attributes.get("type") == "password":
                self.current["password"] = True

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


@contextmanager
def provider():
    name = "energy-oauth-smoke-" + secrets.token_hex(5)
    containers = []
    server = None
    private_directory = ROOT / "private"
    private_directory.mkdir(mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="energy-oauth-smoke-", dir=private_directory) as temporary:
        directory = Path(temporary)
        password = secrets.token_urlsafe(36)
        values = {
            "OAUTH_ISSUER": ISSUER,
            "OAUTH_ALEXA_CLIENT_ID": "home-energy-alexa",
            "OAUTH_ALEXA_CLIENT_SECRET": secrets.token_urlsafe(48),
            "OAUTH_PORTAL_CLIENT_ID": "home-energy-portal",
            "OAUTH_PORTAL_CLIENT_SECRET": secrets.token_urlsafe(48),
            "OAUTH_PORTAL_REDIRECT_URI": PORTAL_REDIRECT,
        }
        realm = GENERATOR.build_realm(values, [ALEXA_REDIRECT])
        realm["users"] = [{
            "username": username,
            "enabled": True,
            "email": username + "@example.test",
            "emailVerified": True,
            "firstName": "Synthetic",
            "lastName": "Household",
            "credentials": [{"type": "password", "value": password, "temporary": False}],
        } for username in ("household-a", "household-b")]
        realm_path = directory / "home-energy-realm.json"
        GENERATOR.write_private(realm_path, realm)
        # The bind-mounted test file contains synthetic credentials only. Its
        # owner-only parent remains private; Keycloak's UID must read the file.
        realm_path.chmod(0o644)
        database_password = secrets.token_urlsafe(48)
        private_text(directory / "postgres.env", f"POSTGRES_DB=keycloak\nPOSTGRES_USER=keycloak\nPOSTGRES_PASSWORD={database_password}\n")
        private_text(directory / "keycloak.env", "\n".join([
            "KC_DB=postgres", f"KC_DB_URL=jdbc:postgresql://{name}-db:5432/keycloak",
            "KC_DB_USERNAME=keycloak", f"KC_DB_PASSWORD={database_password}",
            "KC_HOSTNAME=https://auth.example.test", "KC_HOSTNAME_STRICT=true",
            "KC_HTTP_ENABLED=true", "KC_PROXY_HEADERS=xforwarded",
        ]) + "\n")
        certificate = directory / "tls.pem"
        key = directory / "tls.key"
        openssl = subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-keyout", str(key), "-out", str(certificate), "-subj", "/CN=auth.example.test",
            "-addext", "subjectAltName=DNS:auth.example.test",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if openssl.returncode:
            raise RuntimeError("Could not create the private synthetic TLS test certificate.")
        key.chmod(0o600)
        try:
            docker("network", "create", name)
            db_name = name + "-db"
            containers.append(db_name)
            docker("run", "--detach", "--name", db_name, "--network", name, "--env-file", str(directory / "postgres.env"), "postgres:17.11-bookworm")
            for _ in range(60):
                ready = subprocess.run(["docker", "exec", db_name, "pg_isready", "-U", "keycloak", "-d", "keycloak"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if ready.returncode == 0:
                    break
                time.sleep(1)
            else:
                raise RuntimeError("The disposable PostgreSQL service did not become ready.")
            keycloak_name = name + "-identity"
            containers.append(keycloak_name)
            docker("run", "--detach", "--name", keycloak_name, "--network", name,
                   "--publish", "127.0.0.1::8080", "--env-file", str(directory / "keycloak.env"),
                   "--mount", f"type=bind,src={realm_path},dst=/opt/keycloak/data/import/home-energy-realm.json,readonly",
                   "quay.io/keycloak/keycloak:26.7.3", "start", "--import-realm")
            upstream_port = int(docker("port", keycloak_name, "8080/tcp").rsplit(":", 1)[1])

            class Proxy(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"

                def log_message(self, *arguments):
                    pass

                def forward(self):
                    connection = HTTPConnection("127.0.0.1", upstream_port, timeout=20)
                    length = int(self.headers.get("Content-Length", "0"))
                    body = self.rfile.read(length) if length else None
                    headers = {key: value for key, value in self.headers.items() if key.lower() not in ("host", "connection")}
                    headers.update({"Host": "auth.example.test", "X-Forwarded-Host": "auth.example.test", "X-Forwarded-Proto": "https", "X-Forwarded-Port": "443"})
                    try:
                        connection.request(self.command, self.path, body=body, headers=headers)
                        response = connection.getresponse()
                        data = response.read()
                        self.send_response(response.status)
                        for key, value in response.getheaders():
                            if key.lower() not in ("connection", "transfer-encoding", "content-length"):
                                self.send_header(key, value)
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                    except OSError:
                        self.send_error(503)
                    finally:
                        connection.close()

                do_GET = forward
                do_POST = forward

            server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
            tls_server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            tls_server.load_cert_chain(certificate, key)
            server.socket = tls_server.wrap_socket(server.socket, server_side=True)
            Thread(target=server.serve_forever, daemon=True).start()
            proxy_port = server.server_address[1]
            tls_client = ssl.create_default_context(cafile=str(certificate))

            class TestConnection(HTTPSConnection):
                def connect(self):
                    if self.host != "auth.example.test":
                        raise OSError("Only the disposable identity provider is reachable.")
                    raw_socket = socket.create_connection(("127.0.0.1", proxy_port), timeout=self.timeout)
                    self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)

            class TestTLS(HTTPSHandler):
                def https_open(self, request):
                    return self.do_open(TestConnection, request, context=tls_client)

            def opener(*handlers):
                return build_opener(ProxyHandler({}), TestTLS(), NoRedirects(), *handlers)

            for _ in range(120):
                try:
                    with opener().open(ISSUER + "/.well-known/openid-configuration", timeout=2) as response:
                        discovery = json.load(response)
                    if discovery["issuer"] != ISSUER:
                        raise RuntimeError("The real provider emitted an unexpected issuer.")
                    break
                except (OSError, HTTPError):
                    time.sleep(1)
            else:
                raise RuntimeError("The disposable identity provider did not become ready.")
            config = OAuthConfig(
                issuer=ISSUER, authorization_url=discovery["authorization_endpoint"],
                token_url=discovery["token_endpoint"], introspection_url=discovery["introspection_endpoint"],
                alexa_client_id=values["OAUTH_ALEXA_CLIENT_ID"], alexa_client_secret=values["OAUTH_ALEXA_CLIENT_SECRET"],
                portal_client_id=values["OAUTH_PORTAL_CLIENT_ID"], portal_client_secret=values["OAUTH_PORTAL_CLIENT_SECRET"],
                portal_redirect_uri=PORTAL_REDIRECT,
            )
            yield config, CheckedOAuthClient(config, opener=opener()), opener, password
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()
            for container in reversed(containers):
                subprocess.run(["docker", "rm", "--force", "--volumes", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["docker", "network", "rm", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def authorize(config, make_opener, username, password, role, *, pkce=True, redirect=None, scope=None):
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(32)
    redirect = redirect or (ALEXA_REDIRECT if role == "alexa" else PORTAL_REDIRECT)
    parameters = {
        "response_type": "code", "client_id": getattr(config, role + "_client_id"),
        "redirect_uri": redirect, "scope": scope or ("energy:read" if role == "alexa" else "openid"), "state": state,
    }
    if pkce:
        parameters.update({"code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode(), "code_challenge_method": "S256"})
    browser = make_opener(HTTPCookieProcessor(CookieJar()))
    request = Request(config.authorization_url + "?" + urlencode(parameters))
    for _ in range(8):
        try:
            with browser.open(request, timeout=20) as response:
                html = response.read().decode("utf-8")
                url = response.url
        except HTTPError as error:
            if error.code in (302, 303):
                location = error.headers["Location"]
                error.close()
                if location.startswith(redirect + "?"):
                    query = parse_qs(urlsplit(location).query)
                    if query.get("state") != [state] or "code" not in query:
                        raise RuntimeError("The real OAuth callback did not contain the expected code and state.")
                    return query["code"][0], verifier
                request = Request(location)
                continue
            status = error.code
            error.close()
            if status == 400:
                raise AuthorizationRejected from None
            raise RuntimeError("The real authorization request failed unexpectedly.") from None
        parser = FormParser()
        parser.feed(html)
        forms = [form for form in parser.forms if form["action"]]
        if not forms:
            raise RuntimeError("The real authorization flow did not expose the expected login or consent form.")
        form = forms[0]
        fields = form["fields"]
        if form["password"]:
            fields.update({"username": username, "password": password})
        else:
            fields.pop("cancel", None)
            fields["accept"] = "Yes"
        request = Request(urljoin(url, form["action"]), data=urlencode(fields).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"})
    raise RuntimeError("The real authorization flow exceeded its bounded number of browser steps.")


def token_request(client, role, fields, *, url=None):
    return client._post_json(url or client.config.token_url, fields,
                             getattr(client.config, role + "_client_id"),
                             getattr(client.config, role + "_client_secret"), authorization_code=True)


def expect_rejected(operation, message):
    try:
        operation()
    except (OAuthError, AuthorizationRejected):
        return
    raise RuntimeError(message)


def main():
    with provider() as (config, client, opener, password):
        print("Provider import and verified HTTPS discovery passed.", flush=True)
        identities = {}
        for username in ("household-a", "household-b"):
            code, verifier = authorize(config, opener, username, password, "portal")
            print("Portal authorization code received.", flush=True)
            login = checked(client, "Portal login", lambda: client.exchange_portal_code(code, verifier))
            print("Portal code exchange and token introspection passed.", flush=True)
            identities[username] = login.identity
            code, verifier = authorize(config, opener, username, password, "alexa")
            fields = {"grant_type": "authorization_code", "code": code, "redirect_uri": ALEXA_REDIRECT, "code_verifier": verifier}
            tokens = token_request(client, "alexa", fields)
            identity = checked(client, "Alexa identity", lambda: client.introspect_alexa(tokens["access_token"]))
            print("Alexa code exchange and token introspection passed.", flush=True)
            if identity.subject != login.identity.subject or identity.issuer != login.identity.issuer:
                raise RuntimeError("The two OAuth clients did not resolve the same household identity.")
            expect_rejected(lambda: client.introspect_alexa(login.access_token), "A portal token authorized the Alexa client.")
            expect_rejected(lambda: client.introspect_portal(tokens["access_token"]), "An Alexa token authorized the portal client.")
            refreshed = token_request(client, "alexa", {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
            checked(client, "Refreshed Alexa identity", lambda: client.introspect_alexa(refreshed["access_token"]))
            if refreshed["refresh_token"] == tokens["refresh_token"]:
                raise RuntimeError("The real provider did not rotate its refresh token.")
            expect_rejected(lambda: token_request(client, "alexa", {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}), "A used refresh token was accepted twice.")
            # Authorization-code reuse can revoke its client session. Exercise
            # this destructive check on a separate grant after refresh checks.
            code, verifier = authorize(config, opener, username, password, "alexa")
            replay_fields = {"grant_type": "authorization_code", "code": code, "redirect_uri": ALEXA_REDIRECT, "code_verifier": verifier}
            replay_tokens = token_request(client, "alexa", replay_fields)
            checked(client, "Fresh replay-test identity", lambda: client.introspect_alexa(replay_tokens["access_token"]))
            expect_rejected(lambda: token_request(client, "alexa", replay_fields), "An authorization code was accepted twice.")
        if identities["household-a"].subject == identities["household-b"].subject:
            raise RuntimeError("Distinct households received the same subject.")
        expect_rejected(lambda: authorize(config, opener, "household-a", password, "alexa", pkce=False), "The provider accepted an authorization request without PKCE.")
        expect_rejected(lambda: authorize(config, opener, "household-a", password, "alexa", redirect="https://unregistered.example.test/callback"), "The provider accepted an unregistered redirect.")
        code, verifier = authorize(config, opener, "household-a", password, "alexa", scope="openid")
        tokens = token_request(client, "alexa", {"grant_type": "authorization_code", "code": code, "redirect_uri": ALEXA_REDIRECT, "code_verifier": verifier})
        expect_rejected(lambda: client.introspect_alexa(tokens["access_token"]), "A token without energy:read authorized energy access.")
        incorrect_client = OAuthClient(replace(config, alexa_client_secret=secrets.token_urlsafe(48)), opener=opener())
        expect_rejected(lambda: token_request(incorrect_client, "alexa", {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}), "The provider accepted incorrect client credentials.")
        code, verifier = authorize(config, opener, "household-a", password, "alexa")
        expect_rejected(lambda: token_request(client, "alexa", {"grant_type": "authorization_code", "code": code, "redirect_uri": ALEXA_REDIRECT, "code_verifier": secrets.token_urlsafe(48)}), "The provider accepted the wrong PKCE verifier.")
        code, verifier = authorize(config, opener, "household-a", password, "alexa")
        tokens = token_request(client, "alexa", {"grant_type": "authorization_code", "code": code, "redirect_uri": ALEXA_REDIRECT, "code_verifier": verifier})
        client.introspect_alexa(tokens["access_token"])
        credentials = base64.b64encode((config.alexa_client_id + ":" + config.alexa_client_secret).encode()).decode()
        request = Request(ISSUER + "/protocol/openid-connect/revoke", data=urlencode({"token": tokens["refresh_token"], "token_type_hint": "refresh_token"}).encode(), headers={"Authorization": "Basic " + credentials, "Content-Type": "application/x-www-form-urlencoded"})
        with opener().open(request, timeout=10) as response:
            if response.status != 200:
                raise RuntimeError("The real provider rejected revocation.")
        expect_rejected(lambda: client.introspect_alexa(tokens["access_token"]), "A revoked client session still authorized energy access.")
        expect_rejected(lambda: token_request(client, "alexa", {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]}), "A revoked refresh token was accepted.")
        print("PASS: real Keycloak import, verified TLS, two households, both OAuth clients, code exchange, audience isolation, PKCE, refresh rotation/replay rejection, and revocation.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Do not print provider responses, URLs, codes, tokens, or passwords.
        message = str(error) if type(error) is RuntimeError else type(error).__name__
        raise SystemExit("FAIL: " + message) from None
