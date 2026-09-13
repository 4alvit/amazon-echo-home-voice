# Self-hosted deployment for multiple households

This separate Compose project adds a Keycloak identity provider, PostgreSQL, the Alexa webhook, and a connection portal. It requires an ordinary Linux server with Docker Compose, persistent storage, and a trusted TLS reverse proxy. Run it on a server or capable NAS, not on Cerbo GX. It does not provision cloud services, enable a paid plan, or modify an existing personal deployment.

Keycloak and PostgreSQL are self-hosted open-source software. Their software does not require a paid account; the operator supplies hosting, storage, certificates, and an existing domain. Images are pinned by version and verified multi-platform index digest to [Keycloak 26.7.3](https://www.keycloak.org/downloads) and [PostgreSQL 17.11](https://www.postgresql.org/docs/release/17.11/). Review upstream security releases before a new installation; update versions and digests together after testing.

Read the [account-linking and household guide](../../docs/account-linking.md) before starting. This scaffold is for one server. The household database is SQLite and must be on local storage shared by the two application containers; this is not a stateless Lambda or multi-server deployment.

## Prepare a separate installation

Use a clean working copy and run these commands from this directory. Do not combine this file with the root personal `compose.yaml`.

```bash
python3 prepare_config.py
```

This creates `.env` with mode `0600` and independently generated database, administrator, OAuth-client, and encryption secrets. It never prints secrets and refuses to overwrite an existing `.env`. It also creates the ignored `private/` directory with mode `0700`.

Edit `.env` locally. Replace the example identity hostname, portal hostname, and Alexa skill ID. Keep the authorization, token, and introspection URLs under the same issuer. Values must be literal unquoted `KEY=value` lines, without shell expansion. The generated OAuth secrets already use URL-safe characters; do not replace them with the example placeholders.

Create a **separate development skill** while testing this deployment. Keep the current working skill and backend available until both households pass the acceptance checklist. The backend port defaults to `8091`; if that conflicts with a running personal deployment, use a different host or edit this private deployment's port mapping and reverse proxy together.

In that development skill's **Build → Account Linking** page, copy all the **Alexa Redirect URLs** displayed by Amazon into `private/alexa-redirects.txt`, one exact URL per line. These are Amazon callback URLs, not your portal callback. Keep the real values private. Then generate the realm import:

```bash
python3 generate_realm.py --env-file .env \
  --redirects-file private/alexa-redirects.txt \
  --output private/home-energy-realm.json
```

Alternatively repeat `--alexa-redirect` for each exact callback. The generator rejects non-HTTPS URLs, wildcards, duplicate callbacks, reused client secrets, inconsistent issuer URLs, public output directories, and existing output files. It reads secrets from `.env` or the process environment; it does not take secrets as command-line arguments or print them.

The import contains OAuth client secrets and real callback identifiers. It is mode `0600`, must remain outside Git, and must be readable by the non-root Keycloak container. On the Linux Docker host, prepare ownership:

```bash
sudo chown 1000:0 private/home-energy-realm.json
sudo install -d -m 700 -o 10001 -g 10001 private/data
docker compose config --quiet
docker compose build
docker compose --profile setup run --rm initialize
docker compose up -d postgres keycloak alexa portal
```

The Keycloak image uses UID `1000`; the application image uses UID `10001`. A bind-mounted mode `0600` import owned by a different UID is unreadable in Keycloak. The private host directory can remain owned by the operator: the file itself is mounted into the container. Do not solve permission errors with world-readable credentials. Adjust ownership for rootless Docker's UID mapping where applicable.

The `initialize` command explicitly creates the household database and checks its encryption key. The running application does not silently create a missing database or replace an incompatible key. Keep the same `TENANT_ENCRYPTION_KEY` in both application containers. It is not the OAuth client secret and must not be regenerated on restart.

Keycloak runs `start --import-realm` in production mode with PostgreSQL. It does not use `start-dev` or an ephemeral identity database. Its [startup import](https://www.keycloak.org/server/importExport) skips a realm that already exists: editing or regenerating the JSON does **not** update the existing realm. Make later client, redirect, and secret changes through the private administrative interface after a backup.

## Publish only the intended HTTPS surfaces

The Compose host ports are bound to loopback:

- `127.0.0.1:8085` serves Keycloak behind the identity TLS reverse proxy.
- `127.0.0.1:8091` serves the signature-verified Alexa webhook.
- `127.0.0.1:8092` serves the connection portal.
- PostgreSQL has no published port and is on a private container network.

All external endpoints require valid HTTPS certificates. Set the proxy's upstream scheme/host headers explicitly; never trust client-supplied `X-Forwarded-*` headers. Keycloak's `KC_HOSTNAME` is the complete fixed public HTTPS origin, and `KC_PROXY_HEADERS=xforwarded` assumes the trusted proxy overwrites those headers. See Keycloak's [production](https://www.keycloak.org/server/configuration-production), [hostname](https://www.keycloak.org/server/hostname), and [reverse-proxy](https://www.keycloak.org/server/reverseproxy) guidance.

For the example hostnames:

- Route `auth.example.com/realms/home-energy/`, `/resources/`, and required `/.well-known/` discovery paths to Keycloak. Keep `/admin/`, `/realms/master/`, management port `9000`, metrics, and health endpoints behind a private operator/VPN route. Configure that private administrative route before removing the bootstrap administrator.
- Route `connect.example.com` to the portal. Its supported paths are `/`, `/login`, `/callback`, `/connection`, `/disconnect`, and `/logout`. Browser sessions and credentials require HTTPS. Preserve host and the exact callback path. Do not cache pages or log callback query strings, cookies, form bodies, or authorization headers.
- Route only `POST /alexa` on the chosen voice endpoint to the webhook, preserving Amazon's original signed request bytes and signature headers. Other public paths must stay closed. The existing [Worker relay](../worker-vpc/README.md) handles this single voice path only.

The identity token endpoint must accept Amazon's server-to-server OAuth requests without a browser challenge or Cloudflare Access service token. The portal callback must accept the user's browser redirect. **An operational `/alexa` Worker does not automatically publish Keycloak or the portal.** This scaffold intentionally does not widen that Worker, disable Bot Fight Mode, change shared tunnel routes, or upgrade Cloudflare. Verify an authorized HTTPS route for each new surface separately. Existing network allowlists or Bot Fight Mode can block OAuth as well as voice traffic.

An optional [account-linking Workers VPC relay](../account-linking-vpc/README.md) implements these public path and header boundaries for separate portal and Keycloak Workers. Its local tests use synthetic bindings; publishing or changing a live identity provider is a separate deployment operation.

Apply request-size limits, timeouts, and appropriate abuse controls at the trusted reverse proxy. Rate-limit portal `/login` and identity-provider login/token traffic without blocking legitimate Amazon refreshes. The portal caps pending login transactions at 1,000 per installation and expires them after five minutes; capacity exhaustion fails closed, so this cap is not a replacement for abuse controls. The application enforces its own input limits and CSRF checks; Keycloak's brute-force protection is enabled. Preserve the exact callback URLs and PKCE checks when configuring proxy rules.

## Create accounts and finish setup

The imported realm is named `home-energy`. Registration, password-reset email, password grants, implicit grants, service accounts, and unrestricted cross-client scopes are disabled. No users, demo passwords, or SMTP credentials are imported.

Through the private Keycloak administrative interface:

1. Sign in using the generated bootstrap credentials from the private `.env`. Create a permanent administrator, enable MFA for it, verify access, then remove the bootstrap administrator. Remove its credentials from long-lived operational configuration after adjusting the Compose bootstrap variables accordingly.
2. Create a normal user in `home-energy` for each test household. Assign a unique initial password and require a password change. Do not give household users realm-management roles. Use the same home account in the connection portal and Alexa linking screen.
3. Follow the [Amazon Console settings](../../docs/account-linking.md#configure-the-alexa-development-skill), then connect two different homes and run the acceptance checklist.

For public self-service signup, first configure and verify your own SMTP service, support process, and user-facing privacy/deletion information. In Keycloak, enable registration and **Verify email**, configure password recovery, and test the complete registration/recovery path before accepting public users. Keep registration closed until that works. This template does not supply an email service, send invitations, or invent a business identity. An invitation-only release instead needs a clearly documented account-creation process and reviewer access.

## Persistence, backup, and removal

Back up the PostgreSQL identity database, the household SQLite database, and private configuration. Stop both application containers for a consistent SQLite file copy, or use SQLite's supported backup API. Encrypt backups and store the `TENANT_ENCRYPTION_KEY` separately from database backups. Losing that key makes saved IGW credentials unreadable; recreating accounts can change their subject IDs and will not automatically recover their household connections.

Use PostgreSQL's supported backup/restore procedure for identity records and signing keys. Do not copy a live PostgreSQL data directory as an ordinary file backup. Test restoration in an isolated environment before upgrades. The initial realm JSON is configuration, not a backup of accounts created later.

To stop this separate installation without deleting persistent data:

```bash
docker compose stop
```

Do not run `docker compose down --volumes` unless you intentionally want to delete the identity database. For a user's disconnect, account deletion, or credential rotation, use the [data lifecycle procedures](../../docs/account-linking.md#disconnect-revoke-and-delete). Encryption-key rotation requires a deliberate decrypt/re-encrypt migration; simply replacing the environment key is not rotation.

## Validation status

The realm generator has automated checks for separated audiences/scopes, PKCE-only authorization code flow, rejected unsafe redirects, configuration drift, private non-overwriting output, and secret generation without console disclosure. Compose can be checked without starting containers using `docker compose config --quiet` after private values are configured.

From the repository root, run the opt-in real-provider smoke test with Docker, OpenSSL, and Python 3.11 or newer:

```bash
PYTHONPATH=src python3 tests/integration/keycloak_smoke.py
```

It creates isolated PostgreSQL and Keycloak containers, a synthetic realm with two accounts, and a local HTTPS proxy with a trusted temporary certificate. It exercises both clients through actual browser authorization-code flows, then uses the application's OAuth client for token exchange and introspection. It checks intended audiences, shared subjects within a household, different subjects across households, PKCE and redirect rejection, scopes, client authentication, refresh rotation/replay rejection, and revocation. Authorization-code replay can invalidate a Keycloak client session, so that destructive check uses a separate grant. Containers, test database volumes, and private temporary configuration are removed afterward. A failed run retains a bounded, owner-only diagnostic log in the repository's ignored `private/` directory; remove it after debugging. No live identity provider or Amazon account is used.

A passing local provider test does not establish a real Amazon OAuth exchange, public TLS routing, or physical Echo operation. Complete the [two-household acceptance checklist](../../docs/account-linking.md#acceptance-before-public-release) on the deployment before requesting public certification.
