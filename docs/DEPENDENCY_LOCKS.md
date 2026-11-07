# Dependency locks

The dependency files used by the Amazon voice adapter pin exact package versions and
SHA-256 distribution hashes. CI installs build tools from
`.github/requirements-build.txt` before installing source distributions or this
project with `--no-build-isolation`. This prevents pip from resolving a separate,
unlocked build environment. Application dependencies remain installed from their
full locks; `--no-deps` is used only for the subsequent local project install.

The first two comment lines in each generated lock record its `uv pip compile`
command. Run that command from the repository root with uv 0.12.18 to regenerate
the lock, review version and hash changes, then repeat the corresponding CI
checks before merging. Hashes establish the selected artifact identity; they do
not establish that a package is free of vulnerabilities. Keep dependency
security scanning and update review enabled.

Renovate's native `pip-compile` manager updates each source together with its
recorded compiled outputs, including the Lambda, test, webhook, and unified
runtime locks. `.github/requirements-wheels-only.in` supplies the same
`--only-binary :all:` policy as the former command-line argument, scoped only
to the three compile targets that include it. Other compile flags, Python
versions, package versions, and hashes are preserved.

`python scripts/check_dependency_locks.py` checks the shared cryptography pin
against every deployment lock. The Lambda image additionally runs the checker
with `--installed`, so its assertion follows the source version after updates.

`requirements-webhook.in` preserves the selected webhook dependency versions,
including the oscrypto source commit needed for OpenSSL version parsing. Its
lock additionally verifies the source archive hash. `requirements-test.lock`
contains the multi-household test dependencies for the Python 3.11–3.13 matrix.
Run the complete unit matrix and the webhook image/import tests after changing
these locks. The existing pinned container base remains unchanged.

The webhook lock upgrades urllib3 to 2.8.0 to address its published security
advisories. A 2026-10-08 pip-audit of registry packages found no known findings
in this lock. pip-audit cannot map the direct oscrypto source URL to a registry
version, so that source requires separate review; a zero registry count does
not certify that source commit or the full application.

## Static metadata for the pinned oscrypto source

The existing oscrypto dependency uses immutable archive `d5f3437ed24257895ae1edd9e503cfb352e635a8` (SHA-256 `f40d112bb8b5ca8cd424d64d80b3bd39a48bc1d769206f2c4d79fd9b0618ae74`). Its reviewed `setup.py` declares version 1.3.0 and only `asn1crypto>=1.5.1`, with no Python-version constraint or optional dependencies. The exact-version `tool.uv.dependency-metadata` entry records those declarations so dependency resolution can run with `--no-build` without executing the archive build backend. The source origin, selected version and distribution hash remain unchanged. Recheck the archive metadata and hash together before changing this source pin.
