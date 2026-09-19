"""Bounded, authenticated HTTPS access to centrally formatted energy reports."""

from dataclasses import dataclass, replace
from http.client import HTTPException, HTTPSConnection
import io
import ipaddress
import json
import math
import os
import queue
import re
import socket
import ssl
import threading
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


REPORT_NAMES = ("battery", "solar", "solar_today", "alarms", "status")
STATUSES = frozenset({"fresh", "stale", "unavailable", "unconfigured"})
MAX_RESPONSE_BYTES = 32768
# Includes HTTP headers and chunk framing, in addition to the JSON body limit.
MAX_PUBLIC_WIRE_BYTES = MAX_RESPONSE_BYTES + 16384
MAX_TEXT_LENGTH = 1200
# Safety caveats and active alarms can make the concise report as long as details.
MAX_BRIEF_TEXT_LENGTH = MAX_TEXT_LENGTH
UNAVAILABLE_TEXT = "Home energy data is unavailable right now. Please try again later."


class GatewayError(Exception):
    """A safe error category; never includes tokens, URLs, or remote bodies."""


class TransientGatewayError(GatewayError):
    """A classified, retryable failure of the read-only gateway transport."""


TRANSIENT_HTTP_STATUSES = frozenset({502, 503, 504})


def _valid_text(value, maximum=MAX_TEXT_LENGTH):
    return (
        isinstance(value, str) and bool(value.strip()) and len(value) <= maximum
        and not any(unicodedata.category(char).startswith("C") for char in value)
        and "<" not in value and ">" not in value
    )


def _header_value(value: str, name: str, required: bool = False) -> str:
    if (required and not value) or len(value) > 4096 or any(
        ord(char) < 33 or ord(char) > 126 for char in value
    ):
        raise GatewayError(f"Invalid {name} configuration")
    return value


@dataclass(frozen=True)
class GatewayConfig:
    url: str
    read_token: str
    cf_client_id: str = ""
    cf_client_secret: str = ""
    timeout_seconds: float = 3.0
    max_age_seconds: float = 30.0
    public_only: bool = False

    def __post_init__(self) -> None:
        try:
            parsed = urlsplit(self.url)
            valid = (
                parsed.scheme == "https"
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and parsed.path == "/v1/energy"
                and not parsed.query
                and not parsed.fragment
                and parsed.port in (None, 443)
                and not any(char.isspace() or ord(char) < 32 for char in self.url)
            )
        except ValueError:
            valid = False
        if not valid:
            raise GatewayError("IGW_URL must be an HTTPS /v1/energy endpoint on port 443")
        _header_value(self.read_token, "IGW_READ_TOKEN", required=True)
        _header_value(self.cf_client_id, "CF_ACCESS_CLIENT_ID")
        _header_value(self.cf_client_secret, "CF_ACCESS_CLIENT_SECRET")
        if bool(self.cf_client_id) != bool(self.cf_client_secret):
            raise GatewayError("Cloudflare Access credentials must be configured together")
        if not (0.1 <= self.timeout_seconds <= 4.0):
            raise GatewayError("IGW_TIMEOUT_SECONDS must be between 0.1 and 4")
        if not (1 <= self.max_age_seconds <= 60):
            raise GatewayError("IGW_MAX_AGE_SECONDS must be between 1 and 60")
        if type(self.public_only) is not bool:
            raise GatewayError("Invalid gateway network policy")
        if self.public_only:
            _public_hostname(self.url)

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        try:
            return cls(
                url=os.environ.get("IGW_URL", ""),
                read_token=os.environ.get("IGW_READ_TOKEN", ""),
                cf_client_id=os.environ.get("CF_ACCESS_CLIENT_ID", ""),
                cf_client_secret=os.environ.get("CF_ACCESS_CLIENT_SECRET", ""),
                timeout_seconds=float(os.environ.get("IGW_TIMEOUT_SECONDS", "3")),
                max_age_seconds=float(os.environ.get("IGW_MAX_AGE_SECONDS", "30")),
            )
        except ValueError as exc:
            raise GatewayError("Invalid numeric gateway configuration") from exc


class NoRedirects(HTTPRedirectHandler):
    """Never forward credentials to a redirect target, even on the same host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# The system resolver has no portable timeout. Limit outstanding daemon lookups;
# a stalled resolver must neither exhaust threads nor hold an Alexa request open.
_DNS_SLOTS = threading.BoundedSemaphore(8)
_PERSONAL_REQUEST_SLOTS = threading.BoundedSemaphore(8)
_IPV6_UNICAST = ipaddress.ip_network("2000::/3")
_SPECIAL_NETWORKS = tuple(map(ipaddress.ip_network, (
    "192.0.0.0/24", "192.88.99.0/24", "2001::/23", "2002::/16", "3fff::/20",
)))


def _public_address(value: str):
    """Reject special addresses consistently across supported Python versions."""
    try:
        if "%" in value:
            raise ValueError
        address = ipaddress.ip_address(value)
        allowed = (
            address.is_global
            and not address.is_multicast
            and not address.is_reserved
            and not address.is_loopback
            and not address.is_link_local
            and not address.is_unspecified
            and not any(address in network for network in _SPECIAL_NETWORKS
                        if address.version == network.version)
        )
        if address.version == 6:
            allowed = allowed and address in _IPV6_UNICAST and address.ipv4_mapped is None
        if not allowed:
            raise ValueError
        return address
    except (TypeError, ValueError) as exc:
        raise GatewayError("Gateway must resolve only to public Internet addresses") from exc


def _public_hostname(url: str) -> str:
    hostname = urlsplit(url).hostname or ""
    if len(url) > 2048 or "%" in hostname:
        raise GatewayError("Invalid public gateway hostname")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        # ASCII DNS names avoid URL, Unicode, and TLS name interpretation differences.
        # Internationalized names can be supplied in their ASCII (punycode) form.
        labels = hostname.split(".")
        if (
            len(hostname) > 253 or len(labels) < 2
            or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                       for label in labels)
        ):
            raise GatewayError("Invalid public gateway hostname")
    else:
        _public_address(str(address))
        hostname = str(address)
    return hostname


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise GatewayError("Gateway request timed out")
    return remaining


def _resolve_public(hostname: str, deadline: float) -> tuple[int, tuple]:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        slots = _DNS_SLOTS
        if not slots.acquire(blocking=False):
            raise GatewayError("Gateway resolver is busy")
        result = queue.Queue(maxsize=1)

        def lookup():
            try:
                result.put(socket.getaddrinfo(hostname, 443, socket.AF_UNSPEC,
                                              socket.SOCK_STREAM, socket.IPPROTO_TCP))
            except Exception:
                result.put(None)
            finally:
                slots.release()

        try:
            threading.Thread(target=lookup, daemon=True, name="gateway-dns").start()
        except RuntimeError as exc:
            slots.release()
            raise GatewayError("Gateway resolver is unavailable") from exc
        try:
            records = result.get(timeout=_remaining(deadline))
        except queue.Empty as exc:
            raise GatewayError("Gateway request timed out") from exc
        if not records or len(records) > 64:
            raise GatewayError("Gateway resolution failed")
        addresses = []
        for family, kind, protocol, _, endpoint in records:
            if family not in (socket.AF_INET, socket.AF_INET6):
                raise GatewayError("Gateway resolution failed")
            address = _public_address(endpoint[0])
            if (family == socket.AF_INET) != (address.version == 4):
                raise GatewayError("Gateway resolution failed")
            if kind != socket.SOCK_STREAM or protocol != socket.IPPROTO_TCP:
                raise GatewayError("Gateway resolution failed")
            addresses.append(address)
        # Validate every answer before using one. Never retry a second address.
        address = addresses[0]
    address = _public_address(str(address))
    if address.version == 4:
        return socket.AF_INET, (str(address), 443)
    return socket.AF_INET6, (str(address), 443, 0, 0)


class _DeadlineReader(io.RawIOBase):
    """Apply the original deadline to every read, including HTTP header reads."""

    def __init__(self, transport):
        self.transport = transport
        self.received = 0

    def readable(self):
        return True

    def readinto(self, buffer):
        self.transport.sock.settimeout(_remaining(self.transport.deadline))
        remaining = MAX_PUBLIC_WIRE_BYTES - self.received
        amount = self.transport.sock.recv_into(memoryview(buffer)[:remaining + 1])
        self.received += amount
        if self.received > MAX_PUBLIC_WIRE_BYTES:
            raise GatewayError("Gateway response is too large")
        return amount

    def close(self):
        if not self.closed:
            super().close()
            self.transport.release_reader()


class _DeadlineSocket:
    def __init__(self, sock, deadline):
        self.sock = sock
        self.deadline = deadline
        self.readers = 0
        self.closing = False

    def makefile(self, mode):
        if mode != "rb":
            raise GatewayError("Invalid gateway transport")
        self.readers += 1
        return io.BufferedReader(_DeadlineReader(self))

    def sendall(self, data):
        self.sock.settimeout(_remaining(self.deadline))
        self.sock.sendall(data)

    def close(self):
        self.closing = True
        if not self.readers:
            self.sock.close()

    def release_reader(self):
        self.readers -= 1
        if self.closing and not self.readers:
            self.sock.close()


class _PinnedHTTPSConnection(HTTPSConnection):
    def __init__(self, hostname, endpoint, deadline):
        self.endpoint = endpoint
        self.deadline = deadline
        super().__init__(hostname, port=443, timeout=_remaining(deadline),
                         context=ssl.create_default_context())

    def connect(self):
        family, address = self.endpoint
        sock = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
        try:
            sock.settimeout(_remaining(self.deadline))
            # Numeric address and family were validated together; no second DNS lookup.
            sock.connect(address)
            sock.settimeout(_remaining(self.deadline))
            # The default trust store, hostname verification, and original SNI remain on.
            secured = self._context.wrap_socket(sock, server_hostname=self.host)
            self.sock = _DeadlineSocket(secured, self.deadline)
        except Exception:
            sock.close()
            raise


def _public_body(config: GatewayConfig, headers: dict) -> bytes:
    deadline = time.monotonic() + config.timeout_seconds
    hostname = _public_hostname(config.url)
    endpoint = _resolve_public(hostname, deadline)
    connection = _PinnedHTTPSConnection(hostname, endpoint, deadline)
    try:
        connection.request("GET", "/v1/energy", headers=headers)
        with connection.getresponse() as response:
            return _response_body(response)
    finally:
        connection.close()


def _personal_body(config: GatewayConfig, headers: dict, opener) -> bytes:
    """Bound DNS and slow bodies without unbounded abandoned transport workers."""
    deadline = time.monotonic() + config.timeout_seconds
    slots = _PERSONAL_REQUEST_SLOTS
    if not slots.acquire(blocking=False):
        raise GatewayError("Gateway transport is busy")
    result = queue.Queue(maxsize=1)

    def run():
        try:
            request = Request(config.url, headers=headers, method="GET")
            # Do not inherit proxy settings for authenticated traffic.
            transport = opener or build_opener(ProxyHandler({}), NoRedirects())
            with transport.open(request, timeout=config.timeout_seconds) as response:
                body = _response_body(response)
            result.put((True, body))
        except (GatewayError, URLError, OSError, HTTPException) as exc:
            if isinstance(exc, HTTPError):
                # The caller may already have timed out and never consume this error.
                exc.close()
            result.put((False, exc))
        except Exception:
            result.put((False, GatewayError("Gateway request failed")))
        finally:
            slots.release()

    try:
        threading.Thread(target=run, daemon=True, name="gateway-personal").start()
    except RuntimeError:
        slots.release()
        raise GatewayError("Gateway transport is busy") from None
    try:
        success, value = result.get(timeout=_remaining(deadline))
    except queue.Empty:
        raise GatewayError("Gateway request timed out") from None
    _remaining(deadline)
    if not success:
        raise value
    return value


def _response_body(response) -> bytes:
    if response.status != 200:
        error = TransientGatewayError if response.status in TRANSIENT_HTTP_STATUSES else GatewayError
        raise error("Gateway request failed")
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise GatewayError("Gateway returned an invalid content type")
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise GatewayError("Gateway response is too large")
    return body


def _unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise GatewayError("Invalid gateway response")
        result[key] = value
    return result


def validate_payload(payload: object, *, now: float, max_age_seconds: float) -> dict:
    """Validate the envelope and speech, leaving all energy calculations in IGW."""
    if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int:
        raise GatewayError("Invalid gateway schema")
    if payload["schema_version"] != 1 or type(payload.get("mqtt_connected")) is not bool:
        raise GatewayError("Invalid gateway schema")
    generated_at = payload.get("generated_at")
    if (
        type(generated_at) not in (int, float)
        or not math.isfinite(generated_at)
        or generated_at <= 0
        or not -5 <= now - generated_at <= max_age_seconds
    ):
        raise GatewayError("Gateway response is out of date")
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict) or not all(
        key in metrics for key in ("battery_soc", "solar_power", "solar_today")
    ):
        raise GatewayError("Invalid gateway metrics")
    reports = payload.get("reports")
    if not isinstance(reports, dict):
        raise GatewayError("Invalid gateway reports")
    names = REPORT_NAMES + (("flow",) if "flow" in reports else ())
    for name in names:
        report = reports.get(name)
        if not isinstance(report, dict):
            raise GatewayError("Invalid gateway report")
        status = report.get("status")
        if not isinstance(status, str) or status not in STATUSES:
            raise GatewayError("Invalid gateway report status")
        if status == "fresh" and not payload["mqtt_connected"]:
            raise GatewayError("Inconsistent gateway report status")
        text = report.get("text")
        if not _valid_text(text):
            raise GatewayError("Invalid gateway report text")
    brief = reports["status"].get("brief_text")
    if "brief_text" in reports["status"] and not _valid_text(brief, MAX_BRIEF_TEXT_LENGTH):
        # The full report has already passed validation. An optional enhancement
        # must not discard its warnings or make an older integration unavailable.
        status_report = {key: value for key, value in reports["status"].items() if key != "brief_text"}
        return payload | {"reports": reports | {"status": status_report}}
    return payload


def fetch_energy(config: GatewayConfig, *, opener=None, now=None) -> dict:
    """At most two GETs within one timeout; never retry auth, schema, or TLS errors."""
    deadline = time.monotonic() + config.timeout_seconds
    try:
        result = _fetch_once(config, opener=opener, now=now)
    except TransientGatewayError:
        remaining = deadline - time.monotonic()
        if remaining < 0.1:
            raise
        result = _fetch_once(replace(config, timeout_seconds=min(config.timeout_seconds, remaining)),
                             opener=opener, now=now)
    _remaining(deadline)
    return result


def _fetch_once(config: GatewayConfig, *, opener=None, now=None) -> dict:
    """Perform one bounded GET through the same redirect and network safeguards."""
    headers = {
        "Authorization": f"Bearer {config.read_token}",
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "User-Agent": "IGWEnergyVoice/1.0 (+https://github.com/victron-venus/inverter-gateway)",
    }
    if config.cf_client_id:
        headers["CF-Access-Client-Id"] = config.cf_client_id
        headers["CF-Access-Client-Secret"] = config.cf_client_secret
    try:
        if config.public_only:
            # A caller-provided urllib opener must never bypass the public-only policy.
            if opener is not None:
                raise GatewayError("Public gateways require the pinned HTTPS transport")
            body = _public_body(config, headers)
        else:
            body = _personal_body(config, headers, opener)
        payload = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
        return validate_payload(
            payload,
            now=time.time() if now is None else now,
            max_age_seconds=config.max_age_seconds,
        )
    except HTTPError as exc:
        exc.close()
        error = TransientGatewayError if exc.code in TRANSIENT_HTTP_STATUSES else GatewayError
        raise error("Gateway request failed") from exc
    except (URLError, OSError, TimeoutError, HTTPException) as exc:
        reason = exc.reason if isinstance(exc, URLError) else exc
        error = TransientGatewayError if isinstance(reason, (TimeoutError, ConnectionError)) else GatewayError
        raise error("Gateway request failed") from exc
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise GatewayError("Invalid gateway response") from exc
