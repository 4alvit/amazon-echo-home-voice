# OpenSSF Best Practices evidence

This evidence index supports the project's own Passing and Silver self-assessments. The public badge service is authoritative for awarded status. A planned or partially completed criterion is not an award.

## Project, participation and releases

Python adapter, CLI, optional webhook/account-linking services and deployment/release tooling.

The [README](../README.md) explains installation and interfaces. [CONTRIBUTING](../CONTRIBUTING.md) gives the issue/PR process, standards, test commands and review policy. [GOVERNANCE](../GOVERNANCE.md), [Code of Conduct](../CODE_OF_CONDUCT.md) and [ROADMAP](../ROADMAP.md) record roles, private conduct reporting, priorities and the missing backup-maintainer arrangement. Source is public Git under [MIT](../LICENSE), with third-party notices preserved.

The latest reviewed stable source/release is [v0.3.2](https://github.com/4alvit/amazon-echo-home-voice/releases/tag/v0.3.2); its human-readable notes describe changes, upgrade implications, security scope and exact source. [RELEASING](../RELEASING.md) and [CHANGELOG](../CHANGELOG.md) define future release expectations. This work does not deploy a device or publish a new stable release.

## Testing and analysis

146 unit tests (four optional verifier cases require the full webhook image) and 91 Worker tests passed locally. Required CI separately runs the complete webhook dependency image, Lambda package, Keycloak, Terraform and Python 3.11–3.13.

The [test sources](../tests), [CI workflows](../.github/workflows), [quality limits](quality.md) and [security policy](../SECURITY.md) retain the exact commands and scope. Automated tests execute with assertions. Required analysis includes CodeQL on the maintained languages; practical language/syntax checks remain enabled. New confirmed findings must be fixed or narrowly explained, never hidden by disabling a gate.

The existing v0.3.2 release includes an Atheris fuzz report from source `362ad59a8ef7bd5ab1a7a0cdd2835338ea0d660f`, run [38624965449](https://github.com/4alvit/amazon-echo-home-voice/actions/runs/38624965449). This is dated release evidence, not a fuzz run of the current documentation revision.

## Security and human evidence

[The security design/profile](../docs/tls-policy.md) documents active trust boundaries, default behavior and the complete supported strong-key configuration. Crypto claims apply to that configuration; they do not certify arbitrary third-party infrastructure or deployment settings.

On 16 October 2026 the authenticated issue/advisory inventory contained no non-PR issues or advisories, and Dependabot contained no open alerts. Visible code-scanning findings were Scorecard process findings; an empty advisory inventory is not a claim that no vulnerability can exist. Private GitHub reporting was enabled. History scan: one generic-key match was the fixed public ASK header name `signaturecertchainurl`, not a credential.

The maintainer's secure-design/common-vulnerability knowledge and no outside-GitHub reports in the preceding 12 months are recorded in [quality limits](quality.md). These are human statements; they are not inferred from test results.

## Honest Silver progress

No backup person or tested one-week recovery arrangement exists, so access continuity and bus factor remain unmet. Complete cross-language statement coverage, systematic accessibility/internationalization, independent penetration testing and a full assurance case are not established. See [quality limits](quality.md); individual criteria remain separately assessed.
