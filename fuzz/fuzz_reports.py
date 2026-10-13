import json
import sys
import types
from pathlib import Path

import atheris

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("amazon_echo_home_voice")
package.__path__ = [str(ROOT / "src/amazon_echo_home_voice")]
sys.modules[package.__name__] = package

with atheris.instrument_imports():
    from amazon_echo_home_voice.gateway import GatewayError, validate_payload


def test_one_input(data: bytes) -> None:
    try:
        value = json.loads(data)
    except (ValueError, RecursionError, UnicodeError):
        return
    try:
        accepted = validate_payload(value, now=10000.0, max_age_seconds=30)
    except GatewayError:
        return
    assert validate_payload(accepted, now=10000.0, max_age_seconds=30) == accepted  # nosec B101 - intentional test oracle
    assert -5 <= 10000 - accepted["generated_at"] <= 30  # nosec B101 - intentional test oracle
    assert accepted["schema_version"] == 1  # nosec B101 - intentional test oracle


if __name__ == "__main__":
    atheris.Setup(sys.argv, atheris.instrument_func(test_one_input))
    atheris.Fuzz()
