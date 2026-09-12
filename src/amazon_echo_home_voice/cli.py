"""Smoke-check the same authenticated gateway reader used by Lambda."""

import argparse
import sys

from .gateway import GatewayConfig, GatewayError, REPORT_NAMES, UNAVAILABLE_TEXT, fetch_energy


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", choices=REPORT_NAMES, default="status", nargs="?")
    args = parser.parse_args()
    try:
        payload = fetch_energy(GatewayConfig.from_env())
        report = payload["reports"][args.report]
    except GatewayError as exc:
        print(f"{UNAVAILABLE_TEXT} ({exc})", file=sys.stderr)
        return 1
    print(report["text"])
    print(f"Report status: {report['status']}", file=sys.stderr)
    return 0 if report["status"] == "fresh" else 2


if __name__ == "__main__":
    raise SystemExit(main())
