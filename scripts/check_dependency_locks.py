"""Check that the deployed cryptography versions match the project source."""
from __future__ import annotations

import argparse
from importlib.metadata import version
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]
LOCKS = (
    "src/requirements.txt",
    "requirements-test.lock",
    "requirements-webhook.lock",
    "deploy/unified/requirements.lock",
)


def expected_version(root: Path = ROOT) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    entries = project["dependencies"] + project["optional-dependencies"]["multi-household"]
    versions = {
        match.group(1)
        for entry in entries
        if (match := re.match(r"cryptography==([^;\s]+)", entry))
    }
    if len(versions) != 1:
        raise ValueError("Project cryptography declarations must agree on one exact version")
    return versions.pop()


def check(root: Path = ROOT, installed: str | None = None) -> None:
    expected = expected_version(root)
    for filename in (*LOCKS, "requirements-webhook.in"):
        content = (root / filename).read_text()
        match = re.search(r"^cryptography==([^;\s]+)", content, re.MULTILINE)
        if match is None or match.group(1) != expected:
            raise ValueError(f"{filename}: regenerate cryptography {expected} with its source")
    if installed is not None and installed != expected:
        raise ValueError(f"Installed cryptography {installed} does not match source {expected}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed", action="store_true")
    arguments = parser.parse_args()
    check(installed=version("cryptography") if arguments.installed else None)


if __name__ == "__main__":
    main()
