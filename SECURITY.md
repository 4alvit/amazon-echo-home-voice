# Security policy

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/4alvit/amazon-echo-home-voice/security/advisories/new) to send a confidential report to maintainers. Include the affected version/commit, impact, reproduction and possible mitigations. Remove production credentials and personal data from attachments. Do not publish exploitation details in a public issue before coordinating disclosure.

## Support and response

Security fixes target the current default branch and the latest maintained release, where releases exist. Older versions are not promised backports. Maintainers aim to acknowledge private reports within 14 days, investigate and communicate status within 60 days, and coordinate disclosure with the reporter. Confirmed vulnerabilities with a practical fix receive priority over feature work; publish an advisory and release notes that identify affected versions, mitigation and the fixed version. If a fix takes longer, keep the reporter informed without exposing confidential details.

## Deployment trust boundaries

Treat Alexa requests, browser sessions, OAuth provider responses and IGW payloads as untrusted. Verify Alexa signatures before selecting an account. Multi-household identity must come from validated OAuth claims; never fall back to a personal gateway. Public IGW destinations must retain DNS/IP restrictions, hostname verification and redirect rejection. Encrypt stored household connection credentials and keep the encryption key separate from database backups. Personal mode and deployment templates have different exposure assumptions; follow the account-linking guide.

Use synthetic data for testing. Never attach live tokens, private keys, database exports or household telemetry to public CI artifacts. Report a suspected credential exposure privately and revoke the credential through its issuer. See [CONTRIBUTING.md](CONTRIBUTING.md) for validation and [the evidence index](docs/openssf-evidence.md) for assessment limits.
