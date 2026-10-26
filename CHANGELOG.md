# Changelog

## [0.3.6] - Unreleased

### Changed

- Update the digest-pinned webhook Docker base to Python 3.15. The source package includes the updated build recipe; the release does not distribute or deploy a prebuilt container.
- Refresh the reviewed CI and security action pins while preserving the Python, webhook container and integration checks.

### Upgrade

Use the existing source-package and container build procedure with the new pinned recipe. Preserve local configuration and credentials, and retain the previous immutable package and deployed image for rollback. Release publication does not replace running AWS, Alexa or Cast services.

### Security

Required dependency, source and container checks remain enabled. This maintenance preview does not claim a new application vulnerability fix, live account-linking acceptance or physical voice-device testing.

## [0.3.5]

### Security

Update the webhook and unified-service trust bundle to certifi 2026.10.19, removing the compromised emSign roots described in GHSA-6q48-6c7w-9gw2. The webhook extra also requires the patched minimum version.

### Upgrade

Use the existing package or container update procedure and preserve local configuration. Existing release artifacts remain available for rollback; publication does not deploy services.

## [0.3.4] - 2026-10-20

### Added

- Provide an optional personal-mode image that runs the Alexa webhook and the pinned Cast report service under one supervisor, with separate credentials, bounded startup and shutdown, and shared readiness.
- Include the unified deployment's source pins, hashed Python dependencies, Compose template, staging procedure and rollback instructions.
- Add an opt-in multi-household test deployment with isolated loopback ports, explicitly started services and separate private configuration. The configuration helper can write to a selected existing directory without overwriting an existing environment file.

### Changed

- Exercise unified-service startup, peer cleanup, readiness expiry and Linux child reaping in the existing validation pipeline.
- Refresh GitHub Actions and security workflow dependencies while retaining the required Python, container, worker and Terraform checks.

### Upgrade

The unified image is optional and supports personal Alexa mode only. Preserve existing standalone Compose files, image digests, listener addresses and private configuration before staging it on unused ports. Stop the old listeners before taking over their original endpoints, and retain their images and configuration for rollback. Keep the separate multi-household installation and its identity database unchanged.

This release contains installable source and deployment templates, not a prebuilt image or an automatic deployment. Follow `deploy/unified/README.md` for image verification, staging and explicit cutover. The separate test deployment requires Docker Compose 2.24.4 or newer.

### Security

The unified supervisor separates Alexa and Cast environment variables, rejects unsupported modes and broad listener bindings, and keeps the Cast endpoint on an explicit private LAN address. Its Cast source archive and Python dependencies are pinned and verified during the build. Existing request authentication remains required.

Automated process, dependency and configuration checks do not prove a real Amazon OAuth exchange, signed voice request, Cast playback or target-host latency. Complete the documented deployment-specific acceptance checks before replacing a working installation; never put credentials or identity database backups in the source archive.

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
