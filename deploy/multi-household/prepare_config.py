#!/usr/bin/env python3
"""Create private local configuration with random secrets, without printing them."""

from __future__ import annotations

import base64
import os
from pathlib import Path
import secrets
import sys


def main() -> int:
    directory = Path(__file__).resolve().parent
    values = {
        "POSTGRES_PASSWORD": secrets.token_urlsafe(48),
        "KC_BOOTSTRAP_ADMIN_USERNAME": "initial-admin-" + secrets.token_hex(6),
        "KC_BOOTSTRAP_ADMIN_PASSWORD": secrets.token_urlsafe(48),
        "OAUTH_ALEXA_CLIENT_SECRET": secrets.token_urlsafe(48),
        "OAUTH_PORTAL_CLIENT_SECRET": secrets.token_urlsafe(48),
        "TENANT_ENCRYPTION_KEY": base64.urlsafe_b64encode(os.urandom(32)).decode("ascii"),
    }
    try:
        template = (directory / ".env.example").read_text(encoding="utf-8")
        lines = []
        for line in template.splitlines():
            key = line.partition("=")[0]
            lines.append(f"{key}={values[key]}" if key in values else line)
        private = directory / "private"
        private.mkdir(mode=0o700, exist_ok=True)
        if private.is_symlink() or private.stat().st_mode & 0o077:
            raise ValueError
        descriptor = os.open(directory / ".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
    except (OSError, ValueError):
        print("Configuration creation failed. It never overwrites .env; private must be an owner-only directory.", file=sys.stderr)
        return 1
    print("Private .env created. Edit the example URLs and skill ID before continuing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
