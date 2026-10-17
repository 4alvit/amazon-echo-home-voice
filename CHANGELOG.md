# Changelog

## [0.3.3] - 2026-10-17

### Changed

- Make the optional Keycloak realm's iterated Argon2 policy explicit and verify stored password metadata in the disposable identity-provider integration test.
- Update GitHub Actions dependencies and document standalone HTTPS configuration, reviewed assessment evidence and RC fuzzing process and results.

### Upgrade

Existing Keycloak realms require the documented administrative password-policy update; publishing this package does not modify a running realm. Preserve realm configuration and installation-specific credentials, and retain the previous package for rollback.

### Security

The disposable Keycloak test verifies the configured password-policy metadata. It does not prove migration of passwords in existing realms or acceptance of physical voice devices.

## [0.3.2] - 2026-10-14

### Changed

- Validate each public release candidate with bounded, network-isolated fuzz campaigns against its exact source revision.
- Require positive engine execution counts, instrumented production code and validated seed corpora; retain source-bound reports and diagnostic evidence.
- Run deterministic seed and configuration checks in ordinary CI without starting mutation campaigns on betas.

### Upgrade

Use the existing installation procedure, preserve local configuration and keep the previous accepted package for rollback. This maintenance release updates release validation and does not change the application configuration format. Deployment and applicable device or integration acceptance remain separate from publication.

### Security

RC publication rejects absent or invalid source-bound fuzz reports. The fuzz runner bounds corpus paths, file types and size. No application vulnerability or new target-device acceptance is claimed by these tooling changes.

## [0.3.1]

### Added

- Installable source release archives containing the Python application, integration templates, dependency locks and operator documentation.
- Frozen beta, nightly and RC version plans, package metadata verification and exact-asset build receipts.

### Upgrade

Install the source archive in a fresh environment using the documented locked dependencies. Compare and merge configuration templates; preserve installation-specific credentials and existing service configuration outside the checkout. This release does not deploy services, create cloud resources or configure physical speakers. Keep the previously accepted package and configuration for rollback.

### Security

The release process preserves the existing required validation workflows and publishes only tracked inputs. Local secrets, environment files, deployment state and generated media are excluded by the repository's existing tracking policy. Release receipts verify artifact identity; physical voice behavior and installation-specific access must still be accepted separately.
