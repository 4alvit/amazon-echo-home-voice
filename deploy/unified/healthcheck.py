#!/usr/bin/env python3
"""Read PID 1's bounded, credential-free readiness snapshot."""

import json
import os
from pathlib import Path
import time


# This fixed path is inside the private container tmpfs, not a shared host directory.
def healthy(path=Path("/tmp/unified-status.json")):  # nosec B108
    try:
        data = json.loads(path.read_text())
        age = time.monotonic() - data["updated"]
        os.kill(data["pid"], 0)
        return (data["ready"] is True and 0 <= age <= 30
                and bool(data["children"]) and all(data["children"].values()))
    except (OSError, ValueError, TypeError, KeyError):
        return False


if __name__ == "__main__":
    raise SystemExit(0 if healthy() else 1)
