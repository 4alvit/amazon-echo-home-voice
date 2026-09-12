"""WSGI HTTPS webhook, published through a TLS reverse proxy or Cloudflare Tunnel."""

import json
import os
from urllib.request import ProxyHandler, build_opener

from .gateway import GatewayError, NoRedirects, _unique_object
from .lambda_handler import lambda_handler, validate_event

MAX_REQUEST_BYTES = 32768


def _download_certificate(url: str) -> bytes:
    """Bound only the SDK's transport; its certificate URL/trust checks stay intact."""
    with build_opener(ProxyHandler({}), NoRedirects()).open(url, timeout=2) as response:
        body = response.read(16385)
        if response.status != 200 or len(body) > 16384:
            raise ValueError("Invalid certificate response")
        return body


def verify_signature_and_timestamp(headers: dict, raw_body: str) -> None:
    """Delegate Amazon certificate, SHA-256 signature and timestamp checks to ASK."""
    # Optional dependencies are intentionally isolated from Lambda and the CLI.
    from ask_sdk_core.serialize import DefaultSerializer
    from ask_sdk_model import RequestEnvelope
    from ask_sdk_webservice_support.verifier import RequestVerifier, TimestampVerifier
    from cryptography.hazmat.primitives.hashes import SHA256

    class BoundedRequestVerifier(RequestVerifier):
        def _load_cert_chain(self, cert_url):
            # The official verifier checks the Amazon URL before calling this.
            return _download_certificate(cert_url)

    envelope = DefaultSerializer().deserialize(raw_body, RequestEnvelope)
    BoundedRequestVerifier(
        signature_cert_chain_url_key="signaturecertchainurl",
        signature_key="signature-256",
        hash_algorithm=SHA256(),
    ).verify(headers, raw_body, envelope)
    TimestampVerifier(tolerance_in_millis=150000).verify(headers, raw_body, envelope)


def _respond(start_response, status: str, payload: dict) -> list[bytes]:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    start_response(status, [
        ("Content-Type", "application/json"),
        ("Content-Length", str(len(body))),
        ("Cache-Control", "no-store"),
    ])
    return [body]


def application(environ, start_response):
    """Expose health and authenticated Alexa requests; never expose an unsigned API."""
    path = environ.get("PATH_INFO", "")
    method = environ.get("REQUEST_METHOD", "")
    if path == "/health" and method == "GET":
        configured = bool(os.environ.get("ASK_SKILL_ID"))
        return _respond(start_response, "200 OK", {"status": "ok", "skill_configured": configured})
    if path != "/alexa":
        return _respond(start_response, "404 Not Found", {"error": "Not found"})
    if method != "POST":
        return _respond(start_response, "405 Method Not Allowed", {"error": "POST required"})
    try:
        if environ.get("CONTENT_TYPE", "").split(";", 1)[0].lower() != "application/json":
            raise ValueError("Content type")
        size = int(environ.get("CONTENT_LENGTH", "0"))
        if not 0 < size <= MAX_REQUEST_BYTES:
            raise ValueError("Body size")
        headers = {
            "signaturecertchainurl": environ.get("HTTP_SIGNATURECERTCHAINURL", ""),
            "signature-256": environ.get("HTTP_SIGNATURE_256", ""),
        }
        if not all(headers.values()) or any(len(value) > 8192 for value in headers.values()):
            raise ValueError("Signature required")
        body = environ["wsgi.input"].read(size)
        if len(body) != size:
            raise ValueError("Incomplete body")
        raw_body = body.decode("utf-8")
        event = json.loads(raw_body, object_pairs_hook=_unique_object)
        # Cheap checks prevent arbitrary traffic from triggering certificate downloads.
        validate_event(event)
    except (ValueError, TypeError, UnicodeError, KeyError, GatewayError, PermissionError):
        return _respond(start_response, "400 Bad Request", {"error": "Invalid Alexa request"})
    try:
        verify_signature_and_timestamp(headers, raw_body)
    except ImportError:
        return _respond(start_response, "503 Service Unavailable", {"error": "Verifier unavailable"})
    except Exception:
        # The SDK has several certificate/crypto error types; all fail closed.
        return _respond(start_response, "400 Bad Request", {"error": "Invalid Alexa request"})
    try:
        response = lambda_handler(event, None)
    except PermissionError:
        return _respond(start_response, "400 Bad Request", {"error": "Invalid Alexa request"})
    return _respond(start_response, "200 OK", response)
