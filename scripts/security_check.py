#!/usr/bin/env python3
"""Run the FLOSS security/lint checks and fail closed on incomplete analysis."""

import json
from pathlib import Path
# Only the current interpreter and fixed module commands are executed.
import subprocess  # nosec B404
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ['src', 'deploy', 'scripts/security_check.py']


def main():
    subprocess.run(  # nosec B603
        [sys.executable, "-m", "ruff", "check", "--select", "E9,F63,F7,F82", *SOURCES],
        cwd=ROOT, check=True,
    )
    with tempfile.TemporaryDirectory(prefix="security-report-") as temporary:
        report = Path(temporary) / "bandit.json"
        result = subprocess.run(  # nosec B603
            [sys.executable, "-m", "bandit", "-r", *SOURCES,
             "--format", "json", "--output", str(report)],
            cwd=ROOT, check=False,
        )
        data = json.loads(report.read_text())
        if (result.returncode or not isinstance(data, dict)
                or not isinstance(data.get("errors"), list) or data["errors"]
                or not isinstance(data.get("results"), list) or data["results"]
                or not isinstance(data.get("metrics"), dict)
                or not data["metrics"].get("_totals", {}).get("loc", 0)):
            raise SystemExit("Security analysis failed or was incomplete; run Bandit locally for details.")
    print("FLOSS lint and security analysis passed.")


if __name__ == "__main__":
    main()
