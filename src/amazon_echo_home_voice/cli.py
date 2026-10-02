"""Smoke-check the same authenticated gateway reader used by Lambda."""

import argparse
import sys

from .gateway import (
    CLI_REPORT_NAMES,
    FLOW_UNCONFIGURED_TEXT,
    GatewayConfig,
    GatewayError,
    UNAVAILABLE_TEXT,
    fetch_energy,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", choices=CLI_REPORT_NAMES, default="status", nargs="?")
    args = parser.parse_args(argv)
    try:
        payload = fetch_energy(GatewayConfig.from_env())
        report = payload["reports"].get(args.report)
        if report is None:
            # Gateway validation allows older core-only payloads without optional flow.
            if args.report == "flow":
                print(FLOW_UNCONFIGURED_TEXT)
                print("Report status: unconfigured", file=sys.stderr)
                return 2
            print(f"{UNAVAILABLE_TEXT} (missing report {args.report!r})", file=sys.stderr)
            return 1
    except GatewayError as exc:
        print(f"{UNAVAILABLE_TEXT} ({exc})", file=sys.stderr)
        return 1
    print(report["text"])
    print(f"Report status: {report['status']}", file=sys.stderr)
    return 0 if report["status"] == "fresh" else 2


if __name__ == "__main__":
    raise SystemExit(main())
