# Changelog

## [0.3.2] - 2026-10-14

### Changed

- Validate each public release candidate with bounded, network-isolated fuzz campaigns against its exact source revision.
- Require positive engine execution counts, instrumented production code and validated seed corpora; retain source-bound reports and diagnostic evidence.
- Run deterministic seed and configuration checks in ordinary CI without starting mutation campaigns on betas.

### Upgrade

Use the existing installation procedure, preserve local configuration and keep the previous accepted package for rollback. This maintenance release updates release validation and does not change the application configuration format. Deployment and applicable device or integration acceptance remain separate from publication.

### Security

The release gate rejects absent or invalid fuzz evidence and unsafe seed inputs. No application vulnerability or new target-device acceptance is claimed by these tooling changes.

## [0.3.1]

### Added

- Installable source release archives containing the Python application, integration templates, dependency locks and operator documentation.
- Frozen beta, nightly and RC version plans, package metadata verification and exact-asset build receipts.

### Upgrade

Install the source archive in a fresh environment using the documented locked dependencies. Compare and merge configuration templates; preserve installation-specific credentials and existing service configuration outside the checkout. This release does not deploy services, create cloud resources or configure physical speakers. Keep the previously accepted package and configuration for rollback.

### Security

The release process preserves the existing required validation workflows and publishes only tracked inputs. Local secrets, environment files, deployment state and generated media are excluded by the repository's existing tracking policy. Release receipts verify artifact identity; physical voice behavior and installation-specific access must still be accepted separately.
