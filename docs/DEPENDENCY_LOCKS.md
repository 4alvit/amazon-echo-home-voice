# Dependency locks

The dependency files used by the Amazon voice adapter pin exact package versions and
SHA-256 distribution hashes. CI installs build tools from
`.github/requirements-build.txt` before installing source distributions or this
project with `--no-build-isolation`. This prevents pip from resolving a separate,
unlocked build environment. Application dependencies remain installed from their
full locks; `--no-deps` is used only for the subsequent local project install.

The first two comment lines in each generated lock record its `uv pip compile`
command. Run that command from the repository root with uv 0.12.7 to regenerate
the lock, review version and hash changes, then repeat the corresponding CI
checks before merging. Hashes establish the selected artifact identity; they do
not establish that a package is free of vulnerabilities. Keep dependency
security scanning and update review enabled.

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
