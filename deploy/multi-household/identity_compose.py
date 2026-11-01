#!/usr/bin/env python3
"""Run Compose after checking private, digest-pinned identity image bindings."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

# Digests of the reviewed linux/amd64 security builds, not the upstream defaults.
HARDENED = {
    "keycloak": "sha256:63e1248e81ff9d2136e6fa7bed9ce890b51d70a3b8a752f7933381e3ed1a40bc",
    "postgres": "sha256:461eebbbdae43e2bb4d33e8bdd0df63001be4f4c56c2231f093e469f035e639b",
}
PREVIOUS = {
    "keycloak": "sha256:ff4257d0d64efbe99ed1ddfaf07765cc3c36dc7518bf8324d41961327f441c54",
    "postgres": "sha256:051f7b7b3abdd564d5d1bd1e8c4b9c1b6e77087d1dd22020ede611c096a272e0",
}
REFERENCE = re.compile(r"^[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}$")
MARKER = "identity-security-images.json"


def validate_images(configured, current, required=False, rollback=False):
    """Never silently downgrade a hardened installation to template defaults."""
    if any("-security" in image for image in current.values()):
        required = True
    if any(image.rsplit("@", 1)[-1] in HARDENED.values() for image in current.values()):
        required = True
    expected = PREVIOUS if rollback else HARDENED
    required = required or rollback
    for service in HARDENED:
        image = configured.get(service, "")
        if not REFERENCE.fullmatch(image):
            raise ValueError("Identity images must be explicit digest-pinned references")
        if required and image.rsplit("@", 1)[-1] != expected[service]:
            raise ValueError("Required hardened image binding is absent or unreviewed: " + service)
    return all(configured[name].rsplit("@", 1)[-1] == digest for name, digest in HARDENED.items())


def capture(command, directory):
    result = subprocess.run(command, cwd=directory, capture_output=True, text=True)
    if result.returncode:
        # Compose config and diagnostics can contain secrets. Do not echo them.
        raise RuntimeError("Identity preflight command failed: " + command[0])
    return result.stdout


def ensure_marker_directory(directory):
    """Protect a newly created directory even when DSM inherits broader modes."""
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    else:
        directory.chmod(0o700)
    if directory.is_symlink() or directory.stat().st_mode & 0o077:
        raise ValueError("Identity marker requires an owner-only private directory")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--require-hardened", action="store_true",
                        help="mandatory for an existing hardened installation or disaster recovery")
    parser.add_argument("--rollback-to-previous", action="store_true",
                        help="accept only the exact previous image pair, after restoring its database/configuration")
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="Compose command; omit for a read-only preflight")
    args = parser.parse_args()
    directory = args.directory.resolve()
    compose = ["docker", "compose", "--project-directory", str(directory),
               "--env-file", str(directory / ".env"), "-f", str(directory / "compose.yaml")]
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        if command and command[0] not in {"up", "pull", "stop"}:
            raise ValueError("Only lifecycle subcommands are supported")
        if command and command[0] == "up" and any(arg.split("=", 1)[0] == "--no-recreate" for arg in command[1:]):
            raise ValueError("Image updates must be allowed to recreate containers")
        if command and command[0] == "stop":
            return subprocess.run(compose + command, cwd=directory).returncode
        config = json.loads(capture(compose + ["config", "--format", "json"], directory))
        configured = {name: config["services"][name]["image"] for name in HARDENED}
        current = {}
        for name in HARDENED:
            ids = capture(compose + ["ps", "--all", "--quiet", name], directory).split()
            for identity in ids:
                item = json.loads(capture(["docker", "inspect", identity], directory))[0]
                current[name] = item["Config"]["Image"]
        marker = directory / "private" / MARKER
        if marker.is_symlink():
            raise ValueError("Refuse symlinked identity image marker")
        required = args.require_hardened or marker.exists()
        hardened = validate_images(configured, current, required, args.rollback_to_previous)
        if not command:
            print("Identity image preflight passed; hardened=" + str(hardened).lower())
            return 0
        if hardened and not marker.exists():
            ensure_marker_directory(marker.parent)
            with os.fdopen(os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as output:
                os.fchmod(output.fileno(), 0o600)
                json.dump({"version": 1, "require_hardened": True}, output)
                output.write("\n")
        return subprocess.run(compose + command, cwd=directory).returncode
    except (KeyError, ValueError, RuntimeError, OSError):
        print("Identity preflight failed. Check private image overrides, fixed digests, Docker and marker; no Compose action ran.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
