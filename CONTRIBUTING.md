# Contributing

Read-only Alexa energy reporting, with an optional multi-household account-linking portal.

## Questions, bugs and proposals

Use [GitHub Issues](https://github.com/4alvit/amazon-echo-home-voice/issues) for questions, bug reports and feature proposals. Search existing issues first. Describe the affected version/commit, expected and actual behavior, minimal reproduction and relevant environment. Remove tokens, private endpoints, household identifiers and personal data from examples. Security vulnerabilities use the confidential process in [SECURITY.md](SECURITY.md).

Anyone may propose a change through a pull request. Discuss compatibility or architectural changes in an issue before a large implementation. Maintainers aim to acknowledge actionable reports within 14 days; security reports follow the security policy. No paid support or response-time guarantee is implied.

## Development and validation

Clone the repository, create a branch from `main`, and use the Python version and dependencies declared by the project and CI. Run from the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install '.[multi-household]' PyYAML==6.0.3
bash scripts/ci.sh unit
bash scripts/ci.sh worker
bash scripts/ci.sh syntax
```

The unittest suite exercises intent contracts, signatures, OAuth claims, cross-household isolation, credential storage, SSRF restrictions, portal CSRF and response budgets. The syntax check also requires actionlint 1.7.12. Docker is required for `bash scripts/ci.sh container` and `bash scripts/ci.sh keycloak`; the latter uses synthetic accounts in disposable local services. Terraform validation uses `bash scripts/ci.sh terraform` and does not apply infrastructure.

For a bug fix, add a regression test that fails before the fix and passes afterward. For new functionality, test normal behavior, invalid input and relevant authorization/error paths. Preserve existing checks; do not lower coverage gates or ignore findings merely to obtain a green build. Python code follows the configured formatter/linter where present and normal PEP 8 conventions otherwise. Keep shell, YAML and generated examples compatible with their declared tools.

## Review and compatibility

Keep pull requests focused and explain the problem, resulting behavior, compatibility impact and exact validation performed. Update the user-facing documentation when changing configuration, interfaces or operational behavior. Call out tests not run and their prerequisites. Maintainers review changes through GitHub pull requests and required CI; automated review is supplemental. Contributions are provided under the repository's [MIT license](LICENSE); a contributor must have the right to submit the work.

Published releases identify the application source and verified assets; see [RELEASING.md](RELEASING.md) and [versioning](docs/VERSIONING.md). Record user-visible and security changes, affected versions and upgrade actions in each release.

## Source and interfaces

- [`src/amazon_echo_home_voice`](src/amazon_echo_home_voice)
- [`deploy/worker-vpc`](deploy/worker-vpc)
- [`deploy/account-linking-vpc`](deploy/account-linking-vpc)
- [`README.md`](README.md)
- [`architecture.md`](architecture.md)
- [`docs/account-linking.md`](docs/account-linking.md)
- [`docs/utterance-catalog.md`](docs/utterance-catalog.md)

See the [OpenSSF evidence index](docs/openssf-evidence.md) for the current assessment scope and outstanding verification.

## FLOSS static analysis

Install hash-verified analysis dependencies with
`python3 -m pip install --require-hashes --only-binary=:all: -r .github/requirements-security.txt`,
then run `python3 scripts/security_check.py`. The required quality gate runs the
same Ruff and Bandit checks. Scanner failures, empty/incomplete reports and
findings fail the gate. Any narrowly scoped exception must have an explanatory
source comment and a reviewer must verify that it is not an exploitable finding.

## Coding standards and community

Python follows PEP 8 and explicit error handling. Go follows `gofmt` and `go vet` where applicable. JavaScript uses the existing module format, explicit bounds and the repository's lint/test conventions. Shell uses quoted expansions and `set -euo pipefail` for Bash entry points. Preserve third-party notices and the supported runtime versions. These are written standards; not every style rule is automatically enforced.

See [governance](GOVERNANCE.md), the [Code of Conduct](CODE_OF_CONDUCT.md), [roadmap](ROADMAP.md) and [quality limits](docs/quality.md).
