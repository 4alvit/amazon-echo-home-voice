# Documentation

The Alexa adapter that reads energy reports from an IGW installation, plus the
release tooling and evidence records that qualify each published source archive.

## Using the skill

- [Supported English (US) requests](utterance-catalog.md) — the invocation name and the phrases the skill accepts.
- [Concise reports and interactive screens](voice-experience.md) — what the skill says and shows, and which decisions stay with IGW.
- [Account linking and one IGW connection per home](account-linking.md) — multi-household linking and how a home connects its own IGW.

## Release operation

- [CI and release operations](release-workflow.md) — `.release-policy.json` as source of truth and what the quality gate runs.
- [CI and release policy](CI_WORKFLOW.md) — which events run the Quality gate and which validation workflows are selected.
- [Source release packaging and acceptance](release-packaging.md) — the installable Python archive and its Alexa artefacts.
- [Version plans and manifest synchronization](VERSIONING.md) — the `versioning` opt-in in `.release-policy.json`.
- [Dependency locks](DEPENDENCY_LOCKS.md) — the exact package versions the adapter pins.

## Evidence and limits

- [Quality and assessment limits](quality.md) — what the release checks cover and what they do not.
- [Validation summary — 2026-09-12](validation-2026-09-12.md) — completed checks recorded without deployment or device identifiers.
- [TLS certificate key policy](tls-policy.md) — the verification owned HTTPS clients perform.
- [Release-candidate fuzz testing](fuzzing.md) — the illustrated RC fuzzing process and its rollout results.
- [Release read recovery and rerun validation](release-read-safety.md) — the two fixes carried from the shared release toolkit.
- [Early final-build runner check](runner-preflight.md) — why the runner check runs before the final build.
- [OpenSSF Best Practices evidence](openssf-evidence.md) — the evidence index behind the self-assessment.
