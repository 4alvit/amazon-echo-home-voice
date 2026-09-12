"""Bounded, authenticated HTTPS access to centrally formatted energy reports."""

from dataclasses import dataclass
from http.client import HTTPException
import json
import math
import os
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


REPORT_NAMES = ("battery", "solar", "solar_today", "alarms", "status")
STATUSES = frozenset({"fresh", "stale", "unavailable", "unconfigured"})
MAX_RESPONSE_BYTES = 32768
MAX_TEXT_LENGTH = 1200
UNAVAILABLE_TEXT = "Home energy data is unavailable right now. Please try again later."


class GatewayError(Exception):
    """A safe error category; never includes tokens, URLs, or remote bodies."""


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
    for name in REPORT_NAMES:
        report = reports.get(name)
        if not isinstance(report, dict):
            raise GatewayError("Invalid gateway report")
        status = report.get("status")
        if not isinstance(status, str) or status not in STATUSES:
            raise GatewayError("Invalid gateway report status")
        if status == "fresh" and not payload["mqtt_connected"]:
            raise GatewayError("Inconsistent gateway report status")
        text = report.get("text")
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > MAX_TEXT_LENGTH
            or any(unicodedata.category(char).startswith("C") for char in text)
            or "<" in text
            or ">" in text
        ):
            raise GatewayError("Invalid gateway report text")
    return payload


def fetch_energy(config: GatewayConfig, *, opener=None, now=None) -> dict:
    """One GET, no retries or redirects; the caller can fit Alexa's response budget."""
    headers = {
        "Authorization": f"Bearer {config.read_token}",
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "User-Agent": "IGWEnergyVoice/1.0 (+https://github.com/victron-venus/inverter-gateway)",
    }
    if config.cf_client_id:
        headers["CF-Access-Client-Id"] = config.cf_client_id
        headers["CF-Access-Client-Secret"] = config.cf_client_secret
    request = Request(config.url, headers=headers, method="GET")
    # Avoid inheriting proxy configuration that could redirect authenticated traffic.
    opener = opener or build_opener(ProxyHandler({}), NoRedirects())
    try:
        with opener.open(request, timeout=config.timeout_seconds) as response:
            if response.status != 200:
                raise GatewayError("Gateway request failed")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                raise GatewayError("Gateway returned an invalid content type")
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise GatewayError("Gateway response is too large")
        payload = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
        return validate_payload(
            payload,
            now=time.time() if now is None else now,
            max_age_seconds=config.max_age_seconds,
        )
    except HTTPError as exc:
        exc.close()
        raise GatewayError("Gateway request failed") from exc
    except (URLError, OSError, TimeoutError, HTTPException) as exc:
        raise GatewayError("Gateway request failed") from exc
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise GatewayError("Invalid gateway response") from exc
