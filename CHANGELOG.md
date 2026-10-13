# Changelog

## [0.3.0]

### Added

- Installable source release archives containing the Python application, integration templates, dependency locks and operator documentation.
- Frozen beta, nightly and RC version plans, package metadata verification and exact-asset build receipts.

### Upgrade

Install the source archive in a fresh environment using the documented locked dependencies. Compare and merge configuration templates; preserve installation-specific credentials and existing service configuration outside the checkout. This release does not deploy services, create cloud resources or configure physical speakers. Keep the previously accepted package and configuration for rollback.

### Security

The release process preserves the existing required validation workflows and publishes only tracked inputs. Local secrets, environment files, deployment state and generated media are excluded by the repository's existing tracking policy. Release receipts verify artifact identity; physical voice behavior and installation-specific access must still be accepted separately.
