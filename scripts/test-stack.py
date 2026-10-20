#!/usr/bin/env python3
"""Manage only the explicitly selected development stack; never delete its data."""

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "deploy" / "test"


def command(arguments):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "initialize", "up", "status", "stop"))
    parser.add_argument("--env-file", type=Path, default=DIRECTORY / ".env")
    parser.add_argument("--project-name", default="home-energy-test")
    # An accidental bare invocation only displays help; it never checks secrets
    # or selects a Docker action.
    args = parser.parse_args(arguments or ["--help"])
    # Never accept the existing personal or production multi-household project.
    if not re.fullmatch(r"home-energy-test(?:-[a-z0-9][a-z0-9_-]*)?", args.project_name):
        parser.error("project name must be home-energy-test or home-energy-test-<suffix>")
    env_file = args.env_file.expanduser().resolve()
    if not env_file.is_file():
        parser.error("the explicitly selected environment file does not exist")
    base = ["docker", "compose", "--project-name", args.project_name,
            "--project-directory", str(DIRECTORY), "--env-file", str(env_file),
            "--file", str(DIRECTORY / "compose.yaml")]
    profile = "setup" if args.action == "initialize" else "test"
    actions = {
        "check": ["config", "--quiet"],
        "initialize": ["run", "--rm", "initialize"],
        "up": ["up", "--detach", "postgres", "keycloak", "alexa", "portal"],
        "status": ["ps", "--all"],
        "stop": ["stop", "postgres", "keycloak", "alexa", "portal"],
    }
    return base + ["--profile", profile] + actions[args.action]


def main(arguments=None):
    argv = command(sys.argv[1:] if arguments is None else arguments)
    # Compose process variables normally override --env-file values. Keep the
    # selected file authoritative and ignore ambient COMPOSE_* / application
    # configuration; preserve only Docker connection/client and OS necessities.
    keep = {"PATH", "HOME", "USER", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT",
            "SSH_AUTH_SOCK", "XDG_RUNTIME_DIR"}
    environment = {key: value for key, value in os.environ.items()
                   if key in keep or key.startswith("DOCKER_")}
    try:
        return subprocess.run(argv, env=environment, check=False).returncode
    except FileNotFoundError:
        print("Docker with the Compose plugin is required.", file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
