#!/usr/bin/env python3
"""Fetch only the reviewed Cast archive, verifying its content before extraction."""

import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import urllib.request


def main():
    pin = json.loads(Path(__file__).with_name("source-pin.json").read_text())
    revision = pin["revision"]
    expected_url = "https://codeload.github.com/4alvit/google-home-voice-stats/tar.gz/" + revision
    if len(revision) != 40 or pin["archive_url"] != expected_url:
        raise ValueError("Invalid source pin")
    # The URL is the exact reviewed HTTPS archive origin; its content is hashed below.
    with urllib.request.urlopen(expected_url, timeout=60) as response:  # nosec B310
        archive = response.read(8 * 1024 * 1024 + 1)
    if len(archive) > 8 * 1024 * 1024 or hashlib.sha256(archive).hexdigest() != pin["sha256"]:
        raise ValueError("Cast archive checksum mismatch")
    destination = Path(sys.argv[1])
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
        prefix = "google-home-voice-stats-" + revision + "/"
        members = bundle.getmembers()
        if any(not member.name.startswith(prefix.rstrip("/")) for member in members):
            raise ValueError("Unexpected archive root")
        bundle.extractall(destination, filter="data")


if __name__ == "__main__":
    main()
