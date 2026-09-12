"""Resolve authenticated identities to their own household; never use a global fallback."""

from dataclasses import replace
import os
import time

from .gateway import GatewayError
from .oauth import InvalidToken, OAuthClient, OAuthConfig
from .tenant_store import TenantStore


class UnlinkedAccount(Exception):
    pass


class UnconfiguredHome(Exception):
    pass


def mode():
    value = os.environ.get("ENERGY_VOICE_MODE", "personal")
    if value not in {"personal", "multi_household"}:
        raise GatewayError("Invalid voice mode")
    return value


def request_timeout(deadline, maximum, *, reserve=0.1):
    """Reserve response headroom and never start work below its minimum timeout."""
    if deadline is None:
        return maximum
    available = deadline - time.monotonic() - reserve
    if available < 0.1:
        raise GatewayError("Voice request timed out")
    return min(maximum, available)


def household_connection(event, *, deadline=None):
    try:
        # apiAccessToken authorizes Amazon APIs, not our service. Voice-profile tokens
        # are intentionally unused: this version links the owning Amazon account.
        token = event["context"]["System"]["user"]["accessToken"]
    except (KeyError, TypeError):
        raise UnlinkedAccount() from None
    try:
        config = OAuthConfig.from_env()
        if deadline is not None:
            # Two bounded SQLite reads and a short gateway attempt still need time.
            config = replace(config, timeout_seconds=request_timeout(
                deadline, config.timeout_seconds, reserve=0.75))
        identity = OAuthClient(config).introspect_alexa(token)
    except InvalidToken:
        raise UnlinkedAccount() from None
    request_timeout(deadline, 1.0)
    connection = TenantStore.from_env().connection(identity.issuer, identity.subject)
    request_timeout(deadline, 1.0)
    if connection is None:
        raise UnconfiguredHome()
    return connection
