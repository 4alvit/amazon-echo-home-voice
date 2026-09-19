"""Bounded operational diagnostics without request, credential, or report content."""

from http.client import HTTPException
import json
import logging
import socket
import ssl
from urllib.error import HTTPError, URLError

from .gateway import REPORT_NAMES, STATUSES


LOGGER = logging.getLogger(__name__)
# Match only complete, locally defined messages. Unknown text is never emitted.
FAILURE_CATEGORIES = {
    "configuration": (
        "Invalid IGW_READ_TOKEN configuration", "Invalid CF_ACCESS_CLIENT_ID configuration",
        "Invalid CF_ACCESS_CLIENT_SECRET configuration",
        "IGW_URL must be an HTTPS /v1/energy endpoint on port 443",
        "Cloudflare Access credentials must be configured together",
        "IGW_TIMEOUT_SECONDS must be between 0.1 and 4",
        "IGW_MAX_AGE_SECONDS must be between 1 and 60",
        "Invalid gateway network policy", "Invalid numeric gateway configuration",
        "Invalid public gateway hostname", "Invalid voice mode",
        "Public gateways require the pinned HTTPS transport",
    ),
    "deadline": ("Voice request timed out", "Gateway request timed out"),
    "network_policy": ("Gateway must resolve only to public Internet addresses",),
    "resolver": ("Gateway resolver is busy", "Gateway resolver is unavailable", "Gateway resolution failed"),
    "transport": ("Gateway request failed", "Invalid gateway transport", "Gateway transport is busy"),
    "response_format": ("Gateway returned an invalid content type", "Gateway response is too large", "Invalid gateway response"),
    "envelope_age": ("Gateway response is out of date",),
    "report_contract": (
        "Invalid gateway schema", "Invalid gateway metrics", "Invalid gateway reports",
        "Invalid gateway report", "Invalid gateway report status",
        "Inconsistent gateway report status", "Invalid gateway report text",
    ),
}
MESSAGE_CATEGORIES = {message: category for category, messages in FAILURE_CATEGORIES.items() for message in messages}
CAUSE_NAMES = (
    (HTTPError, "HTTPError"), (TimeoutError, "TimeoutError"),
    (ssl.SSLCertVerificationError, "SSLCertVerificationError"), (ssl.SSLError, "SSLError"),
    (ConnectionRefusedError, "ConnectionRefusedError"), (ConnectionResetError, "ConnectionResetError"),
    (socket.gaierror, "gaierror"), (URLError, "URLError"), (HTTPException, "HTTPException"),
    (UnicodeError, "UnicodeError"), (ValueError, "ValueError"), (OSError, "OSError"),
)


def gateway_failure(error: Exception) -> None:
    """Log only a finite category, an allowlisted cause, and a valid HTTP status."""
    message = error.args[0] if len(error.args) == 1 and type(error.args[0]) is str else None
    fields = {"event": "gateway_failure", "category": MESSAGE_CATEGORIES.get(message, "unknown")}
    cause = error.__cause__
    # urllib puts DNS/TLS/socket exceptions in reason rather than __cause__.
    if isinstance(cause, URLError) and not isinstance(cause, HTTPError) and isinstance(cause.reason, Exception):
        cause = cause.reason
    fields["cause"] = next((name for kind, name in CAUSE_NAMES if isinstance(cause, kind)), "none" if cause is None else "other")
    if isinstance(cause, HTTPError) and type(cause.code) is int and 100 <= cause.code <= 599:
        fields["http_status"] = cause.code
    LOGGER.warning("energy_voice_diagnostic %s", json.dumps(fields, sort_keys=True, separators=(",", ":")))


def report_statuses(payload: dict) -> None:
    """Record accepted non-fresh states; successful fresh reports stay quiet."""
    statuses = {}
    names = REPORT_NAMES + (("flow",) if "flow" in payload["reports"] else ())
    for name in names:
        value = payload["reports"][name]["status"]
        statuses[name] = value if type(value) is str and value in STATUSES else "unknown"
    if all(status == "fresh" for status in statuses.values()):
        return
    connected = payload["mqtt_connected"]
    fields = {
        "event": "upstream_report_status", "reports": statuses,
        "mqtt_connected": connected if type(connected) is bool else None,
    }
    LOGGER.warning("energy_voice_diagnostic %s", json.dumps(fields, sort_keys=True, separators=(",", ":")))
