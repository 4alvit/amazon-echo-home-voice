#!/usr/bin/env python3
"""Prepare a private, non-applied Cloudflare route plan for the Alexa webhook."""

import argparse
import copy
import fnmatch
import json
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class PlanError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def candidate_config(current: dict, hostname: str, origin: str) -> tuple[dict, int]:
    """Insert only the new exact hostname and preserve every pre-existing rule."""
    if len(hostname) > 253 or "." not in hostname or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in hostname.split(".")
    ):
        raise PlanError("Use a lowercase public DNS hostname without a path or wildcard")
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ("127.0.0.1", "localhost")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.port is None
    ):
        raise PlanError("This plan requires a verified connector-local HTTP origin with an explicit port")
    existing = current.get("ingress")
    if not isinstance(existing, list) or not existing or existing[-1] != {"service": "http_status:404"}:
        raise PlanError("Expected the existing final catch-all to be exactly http_status:404")
    if any(rule.get("hostname") == hostname for rule in existing):
        raise PlanError("The hostname already has a route; refusing to replace it")
    if current.get("originRequest", {}).get("access", {}).get("required"):
        raise PlanError("Tunnel-wide Access enforcement needs separate review for Alexa")
    matching = [
        i for i, rule in enumerate(existing[:-1])
        if not rule.get("hostname") or fnmatch.fnmatchcase(hostname, rule["hostname"])
    ]
    index = min(matching) if matching else len(existing) - 1
    updated = copy.deepcopy(current)
    updated["ingress"][index:index] = [
        {"hostname": hostname, "path": "^/alexa$", "service": origin.rstrip("/")},
        {"hostname": hostname, "service": "http_status:404"},
    ]
    assert updated["ingress"][:index] + updated["ingress"][index + 2:] == existing
    return updated, index


def auth_headers(mode: str) -> dict:
    headers = {"Accept": "application/json", "User-Agent": "IGWEnergyVoice-RoutingPlan/1.0"}
    if mode == "token":
        token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
        if not token:
            raise PlanError("CLOUDFLARE_API_TOKEN is required")
        headers["Authorization"] = "Bearer " + token
    else:
        key = os.environ.get("CLOUDFLARE_API_KEY", "")
        email = os.environ.get("CLOUDFLARE_EMAIL", "")
        if not key or not email:
            raise PlanError("CLOUDFLARE_API_KEY and CLOUDFLARE_EMAIL are required")
        headers.update({"X-Auth-Key": key, "X-Auth-Email": email})
    return headers


def api_get(path: str, headers: dict):
    request = Request("https://api.cloudflare.com/client/v4" + path, headers=headers, method="GET")
    try:
        with build_opener(NoRedirect(), ProxyHandler({})).open(request, timeout=15) as response:
            result = json.load(response)
    except (HTTPError, URLError, OSError, ValueError) as exc:
        raise PlanError("Cloudflare read failed; no changes were applied") from exc
    if not result.get("success"):
        raise PlanError("Cloudflare read failed; no changes were applied")
    return result["result"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("account-id", "zone-id", "tunnel-id", "connector-id", "hostname", "state-dir"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--origin", default="http://127.0.0.1:8091")
    parser.add_argument("--auth", choices=("token", "key"), default="token")
    args = parser.parse_args()
    try:
        for value in (args.account_id, args.zone_id):
            if not re.fullmatch(r"[0-9a-f]{32}", value):
                raise PlanError("Account and zone identifiers must be lowercase hexadecimal IDs")
        for value in (args.tunnel_id, args.connector_id):
            if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value):
                raise PlanError("Tunnel and connector identifiers must be lowercase UUIDs")
        headers = auth_headers(args.auth)
        prefix = "/accounts/" + args.account_id + "/cfd_tunnel/" + args.tunnel_id
        clients = api_get(prefix + "/connections", headers)
        if len(clients) != 1 or clients[0]["id"] != args.connector_id:
            raise PlanError("The tunnel must have exactly the explicitly verified connector")
        before = api_get(prefix + "/configurations", headers)
        updated, index = candidate_config(before["config"], args.hostname, args.origin)
        records = api_get("/zones/" + args.zone_id + "/dns_records?" + urlencode({"name": args.hostname}), headers)
        if records:
            raise PlanError("The DNS hostname already exists; refusing to overwrite it")
        directory = Path(args.state_dir)
        directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        files = {
            "before.json": before,
            "tunnel-candidate.json": {"config": updated},
            "dns-candidate.json": {
                "type": "CNAME", "name": args.hostname,
                "content": args.tunnel_id + ".cfargotunnel.com", "ttl": 1, "proxied": True,
            },
            "plan.json": {
                "account_id": args.account_id, "zone_id": args.zone_id,
                "tunnel_id": args.tunnel_id, "connector_id": args.connector_id,
                "hostname": args.hostname, "expected_version": before["version"],
                "insert_at": index, "requires_exact_skill_id_verified": True,
            },
        }
        for filename, value in files.items():
            with os.fdopen(os.open(directory / filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
                json.dump(value, output, indent=2)
                output.write("\n")
        print("Prepared private backup and candidate files. Nothing was applied.")
        return 0
    except (PlanError, OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, PlanError):
            print(str(exc), file=sys.stderr)
        else:
            print("Unexpected configuration or output directory; nothing was applied.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
