# Reproducible identity security images

The upstream version pins in `../compose.yaml` are portable defaults. Our hardened
installation supplies `KEYCLOAK_IMAGE` and `POSTGRES_IMAGE` through its private
`.env`; registry addresses are not committed. Keep those overrides in recovery
backups and run `../identity_compose.py --require-hardened` before a deployment.
The wrapper also detects an existing security image or its private marker and
rejects a missing or unreviewed override before changing containers.

The November 2026 builds keep the same application/database release:

- `Dockerfile.keycloak`: Keycloak 26.8.1 with Guava 33.7.2-jre, fixing
  [CVE-2026-102554](https://github.com/advisories/GHSA-xxph-c9ww-hj94). The existing
  Quarkus classpath filename is preserved; the JAR's embedded Maven metadata
  identifies the new version. Source and artifact digests are checked at build.
- `Dockerfile.postgres`: PostgreSQL 17.11 on Bookworm with gosu 1.19 rebuilt using
  Go 1.27.3. Only the gosu executable changes. The database major version,
  libraries, extensions and data directory format remain the upstream release.

Build for the server architecture without loading into the developer daemon:

```bash
docker buildx build --platform linux/amd64 -f Dockerfile.keycloak \
  -t keycloak-security:26.8.1-security.1 --output type=docker,dest=keycloak.tar .
docker buildx build --platform linux/amd64 -f Dockerfile.postgres \
  -t postgres-security:17.11-bookworm-security.1 --output type=docker,dest=postgres.tar .
trivy image --input keycloak.tar --scanners vuln --severity HIGH,CRITICAL
trivy image --input postgres.tar --scanners vuln --severity HIGH,CRITICAL
```

A build is not proof of a clean vulnerability scan. The initial November 1 database
reported three unfixed HIGH findings for Keycloak's OS packages (OpenJDK
CVE-2026-91403 and sqlite-libs CVE-2026-119373/CVE-2026-119586). PostgreSQL's Debian
packages also had HIGH findings without available fixed versions. Re-scan the
exact resulting digest and retain the database timestamp and full report; do not
hide these findings with a blanket ignore. Rebuild when supported fixes appear.
The complete final Keycloak scan also retains lower-severity findings in
OpenTelemetry API 1.62.0 (fixed in 1.67.0) and the bundled MariaDB JDBC driver
3.5.10 (fixed in 3.5.11). The maintained installation uses PostgreSQL and does not
configure telemetry export; these dependency families are retained to avoid an
untested partial OpenTelemetry/Quarkus upgrade. This is a documented scope
exception, not a claim that those packages are patched or universally unreachable.

The final November 1 PostgreSQL image scan reports **61 HIGH and 3 CRITICAL**
entries, with **no available HIGH/CRITICAL package fixes** in that scan. The three
CRITICAL entries require different interpretation:

- [CVE-2025-7458, SQLite](https://security-tracker.debian.org/tracker/CVE-2025-7458):
  Bookworm is affected; Debian records a minor issue without a separate security
  advisory. The described trigger requires executing crafted arbitrary SQL in
  SQLite. PostgreSQL itself is not SQLite.
- [CVE-2026-6653, libxml2](https://security-tracker.debian.org/tracker/CVE-2026-6653):
  Bookworm remains affected; Debian postpones the fix as a minor issue. Retain
  this finding and review XML-processing exposure when changing database usage.
- [CVE-2023-45853, zlib](https://security-tracker.debian.org/tracker/CVE-2023-45853):
  Debian explicitly excludes the Bookworm binary package because the vulnerable
  contributed MiniZip code is not built there. Keep this package-specific
  explanation alongside the raw scan rather than suppressing every zlib finding.

Lower-severity available fixes remain for libsasl2 packages, tzdata and gosu's
`github.com/moby/sys/user` / `golang.org/x/sys` dependencies. They were retained
in this scoped compiler/security refresh and are not represented as patched.
Neither this exception nor the scanner's severity establishes exploitability of
all reported paths. Keep the PostgreSQL listener unpublished and restricted to
the identity network; repeat the exact-image scan when Debian/vendor updates are
available. No database major or Debian release change is part of this update.

Before publication, restore a real `pg_dump -Fc` into a disposable database, start
the candidate Keycloak against that copy, and check realm/client/user counts and
OIDC discovery. Run `tests/integration/keycloak_smoke.py` with the two candidate
image overrides to test synthetic authorization-code, PKCE, refresh and revocation
flows. These checks do not log in to or invalidate real users' sessions.

The 2026-11-01 amd64 candidates passed both the real-data restore and synthetic
HTTPS/OAuth checks. Their published manifests are:

- Keycloak: `sha256:63e1248e81ff9d2136e6fa7bed9ce890b51d70a3b8a752f7933381e3ed1a40bc`.
- PostgreSQL: `sha256:461eebbbdae43e2bb4d33e8bdd0df63001be4f4c56c2231f093e469f035e639b`.

These digests are fixed in the deployment guard. Registry addresses remain
private inputs; image config IDs and full scan/restore records belong with the
protected recovery evidence.

Production qualification on 2026-11-01 also preserved the existing database
volume, private configuration, realm/client/user/credential counts and hashes of
their identifiers. Discovery returned HTTP 200 with the original issuer after
startup. The synthetic test covered verified HTTPS, two households, both OAuth
clients, authorization-code exchange, PKCE, audience isolation, refresh rotation
and replay rejection, and revocation. A real Amazon account-linking exchange,
interactive owner login and physical Echo operation remain separate acceptance
steps. Retain the scan exceptions above when assessing this deployment.

For production, take a fresh logical dump and configuration backup, then update
only the PostgreSQL and Keycloak services. Retain the old image digests and dumps.
If Keycloak's schema migrates, rollback means restore the pre-upgrade dump into a
fresh isolated data volume and start the old image against it; do not start an old
Keycloak directly against a migrated production database. Preserve realm IDs,
subjects, signing keys and client secrets by restoring the database rather than
regenerating the initial realm JSON.

Derive the private health-check port from the existing Docker port binding;
an installation may override the example port. On the tested low-power NAS,
the first production start needed about 174 seconds for Quarkus augmentation
and 62 seconds for application startup. A further temporary HTTP 503 response
said `Bootstrap in progress` and supplied a retry delay. Wait within the reviewed
startup deadline and require successful discovery with the original issuer;
`Listening on` alone does not establish readiness. Investigate persistent 503s
before proceeding to dependent identity services.

The wrapper allows `-- stop keycloak postgres` even when image overrides are
missing. For rollback, first stop Keycloak and restore the pre-upgrade database
into a fresh volume and the matching private configuration. Set both old image
references from the protected backup, then run
`python3 ../identity_compose.py --rollback-to-previous -- up -d postgres keycloak`.
This explicit recovery mode permits only the exact previous Keycloak 26.7.3 and
PostgreSQL 17.11 image digest pair recorded in source. Mixed or arbitrary digests
are rejected. Keep the private marker so later ordinary deployments still require
the reviewed hardened images.
