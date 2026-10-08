# OpenSSF Best Practices evidence

This is an evidence index for the OpenSSF Best Practices Passing self-assessment. It is not an assertion that a badge has been awarded or that every criterion is satisfied. The public badge service is the authority for an awarded status.

## Project and participation

Read-only Alexa energy reporting, with an optional multi-household account-linking portal.

The project is developed publicly in [Git](https://github.com/4alvit/amazon-echo-home-voice) under the [MIT license](../LICENSE). Its source, issue tracker and pull requests are available without a paid account. [Contribution instructions](../CONTRIBUTING.md) describe reporting, changes, coding conventions, tests and review. The [security policy](../SECURITY.md) provides a confidential vulnerability-reporting path, support scope, response targets and deployment boundaries.

## User and interface documentation

- [`README.md`](../README.md)
- [`architecture.md`](../architecture.md)
- [`docs/account-linking.md`](../docs/account-linking.md)
- [`docs/utterance-catalog.md`](../docs/utterance-catalog.md)

## Source, testing and analysis

- [`src/amazon_echo_home_voice`](../src/amazon_echo_home_voice)
- [`deploy/worker-vpc`](../deploy/worker-vpc)
- [`deploy/account-linking-vpc`](../deploy/account-linking-vpc)

- [Test suite](../tests) and [CI workflows](../.github/workflows)
- [Local CI entry point](../scripts/ci.sh)
- [CodeQL analysis](../.github/workflows/codeql.yml)
- [Dependency update configuration](../.github/dependabot.yml)

The unittest suite exercises intent contracts, signatures, OAuth claims, cross-household isolation, credential storage, SSRF restrictions, portal CSRF and response budgets. The syntax check also requires actionlint 1.7.12. Docker is required for `bash scripts/ci.sh container` and `bash scripts/ci.sh keycloak`; the latter uses synthetic accounts in disposable local services. Terraform validation uses `bash scripts/ci.sh terraform` and does not apply infrastructure.

CI results are evidence for the tested revision and environment, not proof of safe production or hardware operation. Check the current default-branch runs and unresolved security findings before answering the analysis criteria. Fuzzing, coverage completeness and independent penetration testing must be supported by actual runs; ordinary unit tests must not be presented as those activities.

## Changes and releases

This is a validation-only repository: `.release-policy.json` does not publish synthetic application releases. Record user-visible and security changes in a release description when publishing a source release. The [release policy](../.release-policy.json) records automation behavior. A new release must identify its source revision and explain notable changes; security fixes must identify relevant advisories when known.

## Criteria still requiring verification

Before submitting or updating the questionnaire, verify the actual project-specific record: responses to bug and enhancement reports, vulnerability reports in every supported channel, release-note history, unresolved scanner findings, dependency status and required review settings. The primary maintainer must personally confirm knowledge of secure design and common implementation vulnerabilities. A confirmation about another repository does not establish these answers here.

Assess transport encryption, credential storage and privilege limits against the implementation and deployment documented in [SECURITY.md](../SECURITY.md). Do not mark a requirement satisfied solely because a policy says it should be. Record justified non-applicability only where the actual architecture supports it. No paid certification, blanket compliance guarantee or third-party audit is claimed.

## Implementation evidence audited on 2026-10-08 UTC

136 tests passed, with four optional ASK-verifier tests skipped, and 91% statement coverage across 1,295 application statements. The default-branch major voice/card change also added tests in commit 34552aa; the current security fix adds six harness regressions. Coverage is statement coverage, not a claim of complete branch or hardware coverage. The unit commands are documented in CONTRIBUTING; the coverage measurement used coverage.py 7.10.7 for the voice projects and the hash-locked pytest-cov toolchain for the generated template.

The supported Python 3.12 runtime was checked with `ssl.create_default_context()`: Python 3.12.14 / OpenSSL 3.5.8 reported a TLS 1.2 minimum, security level 2, required certificate validation, hostname checks and at least 128-bit symmetric ciphers. Offered TLS 1.2 key exchanges were ephemeral ECDHE/DHE; TLS 1.3 was also enabled. The application uses the standard context without enabling obsolete protocol versions or lowering its security level. These are runtime configuration observations, not measurements of a production endpoint. Operators must retain an updated supported runtime and validate their own ingress and devices. See [Python's TLS documentation](https://docs.python.org/3.12/library/ssl.html#ssl.create_default_context).

TLS protects public gateway and OAuth requests. ASK request verification uses SHA-256. Household connection storage uses the `cryptography` library's authenticated Fernet construction (AES-128-CBC with HMAC-SHA-256 and random IVs), not unauthenticated CBC; identity lookup uses HMAC-SHA-256. Browser state/session/CSRF values use `secrets.token_urlsafe`, and PKCE uses SHA-256. The external identity provider owns user passwords; this application stores no password-verifier database. Fernet keys must be generated using `Fernet.generate_key()` as documented; never substitute human-readable passwords. See [Fernet's construction and limits](https://cryptography.io/en/latest/fernet/).

No dedicated fuzzer or dynamic taint tool is currently claimed. The optional dynamic-analysis criterion is explicitly unmet, and complete branch coverage/maximum warning strictness are not claimed. Existing tests execute with assertions enabled. No confirmed medium/high exploitable issue discovered by these runtime tests remains unresolved; future findings follow SECURITY.md.
