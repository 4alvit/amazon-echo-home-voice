#!/usr/bin/env python3
"""Write a private Keycloak realm import from operator-owned configuration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


REALM = "home-energy"


def read_env(path: Path | None) -> dict[str, str]:
    """Read literal KEY=value lines; never evaluate shell code or expansions."""
    values: dict[str, str] = {}
    if path is not None:
        if path.stat().st_mode & 0o077:
            raise ValueError("The environment file must have mode 0600.")
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                raise ValueError("Use literal KEY=value lines in the environment file.")
            if key in values:
                raise ValueError("The environment file contains a duplicate key.")
            if value.startswith(("'", '"')) or "$" in value:
                raise ValueError("Environment values must be unquoted literals without expansions.")
            values[key] = value
    values.update(os.environ)
    return values


def https_url(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise ValueError("A required HTTPS URL is missing or invalid.")
    if any(ord(character) < 33 or ord(character) > 126 for character in value):
        raise ValueError("HTTPS URLs must contain printable ASCII without whitespace.")
    try:
        parts = urlsplit(value)
        valid = (
            parts.scheme == "https"
            and parts.hostname
            and parts.port in (None, 443)
            and parts.username is None
            and parts.password is None
            and not parts.query
            and not parts.fragment
            and "*" not in value
            and "\\" not in value
            and "?" not in value
            and "#" not in value
            and "%" not in parts.netloc
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Use exact HTTPS URLs on port 443 without credentials, queries, fragments, or wildcards.")
    return value


def build_realm(values: dict[str, str], redirects: list[str]) -> dict:
    if not redirects or len(redirects) != len(set(redirects)):
        raise ValueError("Provide at least one exact Alexa redirect URL, without duplicates.")
    redirects = [https_url(value) for value in redirects]
    issuer = https_url(values.get("OAUTH_ISSUER", ""))
    parts = urlsplit(issuer)
    if parts.path != f"/realms/{REALM}":
        raise ValueError("OAUTH_ISSUER must end with /realms/home-energy.")
    origin = f"{parts.scheme}://{parts.netloc}"
    if values.get("KC_HOSTNAME", origin) != origin:
        raise ValueError("KC_HOSTNAME must match the HTTPS origin of OAUTH_ISSUER.")
    for name, suffix in (
        ("OAUTH_AUTHORIZATION_URL", "/protocol/openid-connect/auth"),
        ("OAUTH_TOKEN_URL", "/protocol/openid-connect/token"),
        ("OAUTH_INTROSPECTION_URL", "/protocol/openid-connect/token/introspect"),
    ):
        if values.get(name, issuer + suffix) != issuer + suffix:
            raise ValueError("OAuth endpoint URLs must match OAUTH_ISSUER.")
    portal_redirect = https_url(values.get("OAUTH_PORTAL_REDIRECT_URI", ""))
    if urlsplit(portal_redirect).path != "/callback":
        raise ValueError("The portal redirect URI must end with /callback.")
    clients = []
    for role, allowed_redirects in (("ALEXA", redirects), ("PORTAL", [portal_redirect])):
        client_id = values.get(f"OAUTH_{role}_CLIENT_ID", "")
        secret = values.get(f"OAUTH_{role}_CLIENT_SECRET", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", client_id):
            raise ValueError("Both OAuth client IDs must use letters, numbers, hyphens, or underscores.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", secret) or secret.startswith("replace-with"):
            raise ValueError("Both OAuth client secrets must contain 32 to 256 random URL-safe characters.")
        clients.append({
            "clientId": client_id,
            "name": "Home Energy Alexa" if role == "ALEXA" else "Home Energy connection portal",
            "protocol": "openid-connect",
            "enabled": True,
            "publicClient": False,
            "clientAuthenticatorType": "client-secret",
            "secret": secret,
            "standardFlowEnabled": True,
            "implicitFlowEnabled": False,
            "directAccessGrantsEnabled": False,
            "serviceAccountsEnabled": False,
            "fullScopeAllowed": False,
            "consentRequired": role == "ALEXA",
            "redirectUris": allowed_redirects,
            "webOrigins": [],
            "defaultClientScopes": [],
            "optionalClientScopes": ["energy:read"] if role == "ALEXA" else [],
            "attributes": {"pkce.code.challenge.method": "S256"},
            "protocolMappers": [{
                "name": "Intended client audience",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-audience-mapper",
                "consentRequired": False,
                "config": {
                    "included.client.audience": client_id,
                    "access.token.claim": "true",
                    "id.token.claim": "false",
                    "introspection.token.claim": "true",
                },
            }, {
                "name": "Stable household subject",
                "protocol": "openid-connect",
                "protocolMapper": "oidc-sub-mapper",
                "consentRequired": False,
                "config": {
                    "access.token.claim": "true",
                    "introspection.token.claim": "true",
                },
            }],
        })
    if clients[0]["clientId"] == clients[1]["clientId"] or clients[0]["secret"] == clients[1]["secret"]:
        raise ValueError("Alexa and the portal must have different client IDs and secrets.")
    return {
        "realm": REALM,
        "displayName": "Home Energy",
        "enabled": True,
        "sslRequired": "all",
        "registrationAllowed": False,
        "resetPasswordAllowed": False,
        "rememberMe": False,
        "loginWithEmailAllowed": False,
        "duplicateEmailsAllowed": False,
        "editUsernameAllowed": False,
        "bruteForceProtected": True,
        "failureFactor": 5,
        "permanentLockout": False,
        "maxFailureWaitSeconds": 900,
        "waitIncrementSeconds": 60,
        "minimumQuickLoginWaitSeconds": 60,
        "quickLoginCheckMilliSeconds": 1000,
        "maxDeltaTimeSeconds": 43200,
        "passwordPolicy": "length(14) and notUsername(undefined)",
        "accessTokenLifespan": 300,
        "accessCodeLifespan": 60,
        "accessCodeLifespanLogin": 300,
        "ssoSessionIdleTimeout": 2592000,
        "ssoSessionMaxLifespan": 7776000,
        "revokeRefreshToken": True,
        "refreshTokenMaxReuse": 0,
        "eventsEnabled": False,
        "adminEventsEnabled": False,
        "clients": clients,
        "clientScopes": [{
            "name": "energy:read",
            "description": "Read the energy reports for the home connected to your account.",
            "protocol": "openid-connect",
            "attributes": {
                "include.in.token.scope": "true",
                "display.on.consent.screen": "true",
                "consent.screen.text": "Read energy reports for your connected home",
            },
            "protocolMappers": [],
        }],
        "defaultDefaultClientScopes": [],
        "defaultOptionalClientScopes": [],
    }


def write_private(path: Path, realm: dict) -> None:
    """Never overwrite a realm export or follow an existing output symlink."""
    if not path.parent.is_dir() or path.parent.stat().st_mode & 0o077:
        raise ValueError("Create an owner-only output directory with mode 0700 first.")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(realm, output, indent=2)
        output.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Private literal KEY=value configuration file")
    parser.add_argument("--alexa-redirect", action="append", default=[], help="Exact redirect URL displayed by the Alexa console")
    parser.add_argument("--redirects-file", type=Path, help="One exact Alexa redirect URL per line")
    parser.add_argument("--output", required=True, type=Path, help="New private realm JSON file; existing files are never overwritten")
    arguments = parser.parse_args()
    try:
        redirects = list(arguments.alexa_redirect)
        if arguments.redirects_file is not None:
            redirects.extend(line.strip() for line in arguments.redirects_file.read_text(encoding="utf-8").splitlines() if line.strip())
        write_private(arguments.output, build_realm(read_env(arguments.env_file), redirects))
    except (OSError, ValueError) as error:
        # Operating-system exception strings can include private file names.
        message = str(error) if isinstance(error, ValueError) else "Could not read configuration or create a new private output file."
        print(f"Realm generation failed: {message}", file=sys.stderr)
        return 1
    print("Private realm import created. Keep this file out of version control.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
