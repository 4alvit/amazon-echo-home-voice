# Account linking and one IGW connection per home

Multi-household mode lets each home connect its own IGW to the same Alexa skill. A user signs in to the connection portal, saves their home's read-only IGW connection, and links that same service account in the Alexa app. Alexa receives an OAuth access token; it never receives the IGW token or Cloudflare service credentials.

The existing personal mode still uses one operator-configured gateway. Set `ENERGY_VOICE_MODE=multi_household` for a shared service and follow the [separate self-hosted deployment](../deploy/multi-household/README.md). Do not publish personal mode as a shared skill. Switching modes does not migrate the old global gateway into a user account.

```text
Home A: Cerbo GX → IGW A ─┐
                          ├← authenticated report GET ← Alexa backend ← signed Alexa request
Home B: Cerbo GX → IGW B ─┘                              ↑
                                                        │ token introspection
Connection portal → encrypted household database         │
       ↑                                                Keycloak
       └──────── authorization code with PKCE ────────────┘
```

Cerbo GX continues to publish telemetry. IGW supplies the reports. The identity service, connection database, portal, and Alexa backend run externally; Home Assistant is not required.

## Identity and isolation

Keycloak owns login, passwords, authorization codes, PKCE, access tokens, refresh tokens, and consent. The application does not implement a password database or accept an Amazon user ID as proof of authorization.

Two confidential OAuth clients have separate credentials and audiences:

- `home-energy-alexa` uses authorization code grant with S256 PKCE and the `energy:read` scope. Only this client's valid tokens can authorize Alexa energy reports.
- `home-energy-portal` uses authorization code grant with S256 PKCE and `openid`. Its tokens authorize portal sessions and connection changes; they cannot be used as Alexa tokens.

The backend introspects the linked token against the configured provider and checks that it is active, unexpired, and has the expected issuer, client, audience, subject, and scope. The immutable issuer/subject identifies the connection owner. Keycloak must emit the same subject for both clients; retain the supplied subject mapper and do not add pairwise subject mapping. It selects that owner's connection only. Missing or invalid account linking prompts the user to link their account, and a missing connection prompts setup. There is no fallback to a global IGW credential in multi-household mode.

The portal uses server-side sessions, a secure browser cookie, single-use login state, and CSRF protection. Connection reads and mutations recheck the provider token. The portal does not display saved tokens back to the browser. Gateway credentials are encrypted in the local SQLite database with the operator's Fernet key. Both application processes require that database and the same key; secrets, access tokens, and household records must stay out of source control and public logs.

Each service account currently owns **one** IGW connection. Replacing it changes the home read by all Amazon accounts linked to that service account. This version does not model family-member invitations, shared ownership, several homes under one account, or voice-based selection of a home. Give unrelated households separate service accounts. Only share an account within a household when everyone should have access to the same energy reports.

## Configure the Alexa development skill

Create a separate development skill for the shared deployment. Import and build the existing English (US) interaction model. Keep the existing skill's live endpoint and personal installation unchanged while validating this one.

In **Build → Account Linking**:

1. Enable **Do you allow users to create an account or link to an existing account with you?**
2. Disable **Allow users to enable the skill without account linking** for a service that requires a connected home for its energy reports. Leave app-to-app, mobile-app, and voice linking disabled; this implementation uses standard browser linking.
3. Select **Auth Code Grant** and enable **PKCE Authorization** using SHA-256/S256.
4. Set **Your Web Authorization URI** to `https://auth.example.com/realms/home-energy/protocol/openid-connect/auth`.
5. Set **Access Token URI** to `https://auth.example.com/realms/home-energy/protocol/openid-connect/token`.
6. Set **Your Client ID** to the private deployment's `OAUTH_ALEXA_CLIENT_ID`, normally `home-energy-alexa`. Copy its `OAUTH_ALEXA_CLIENT_SECRET` securely into **Your Secret**. Choose the authentication scheme that sends credentials in an **HTTP Basic** header.
7. Add the single scope **`energy:read`**. Leave the domain list empty unless the actual login page retrieves assets from another domain. Keycloak returns `expires_in`; if the console requires a fallback expiry, use `300` seconds to match the provided realm.
8. Copy all **Alexa Redirect URLs** displayed by this skill into the private realm generator input. Register those exact values on the Alexa client; never use a wildcard or the portal callback here. The portal client separately allows exactly `https://connect.example.com/callback`.
9. Save. Configure the skill's HTTPS endpoint to the new shared webhook's exact `/alexa` URL and enable **Development** testing.

Replace every example hostname with the operator's configured HTTPS hostname. Never paste IGW read tokens, Cloudflare credentials, the portal client secret, or the database encryption key into Amazon's configuration.

Amazon documents the current [authorization-code settings](https://developer.amazon.com/docs/alexaplus/account-linking/configure-authorization-code-grant.html), [authorization flow](https://developer.amazon.com/docs/alexaplus/account-linking/implement-auth-flow.html), and [custom-skill access tokens](https://developer.amazon.com/en-US/docs/alexa/account-linking/add-account-linking-logic-custom-skill.html). Labels can change. After a custom skill is certified and published with account linking, Amazon does not let you disable linking for that skill; validate the intended experience before certification.

## Connect a home and use the skill

For each household:

1. Obtain a service account from the operator, or register through the operator's verified signup flow when it is available. An Amazon account and a Home Energy service account are separate accounts.
2. Open the operator's connection portal, for example `https://connect.example.com`, and sign in. The browser goes to Keycloak for login; the portal does not ask for the service password itself.
3. Enter your own IGW endpoint, for example `https://energy.example.com/v1/energy`, and a dedicated read-scoped IGW token. If that endpoint uses Cloudflare Access, enter both service-token values for this home's route. Do not use an IGW write or administrative token.
4. Save the connection. The portal checks that the endpoint returns a valid energy envelope before saving. A failed check keeps the existing saved connection. All endpoints must be public HTTPS on port 443 with exactly `/v1/energy`, no embedded credentials, query, fragment, or redirect. Private/local addresses are rejected. Protect the public IGW route using its scoped token and, if configured, Cloudflare Access.
5. Open the Alexa app, find the development skill in **Your Skills → Dev**, choose **Enable to Use**, and complete account linking by signing in to the **same service account** used in the portal. For a certified version, users would enable its store listing instead. The developer's private skill is not automatically visible to unrelated Amazon accounts; use Amazon's supported beta-test access while testing a second household.
6. Accept consent to read energy reports. Say **Alexa, ask home energy for battery status**. Then test solar power, solar energy today, alarm status, and system status using the [voice-command guide](../README.md#everyday-voice-commands).

The Echo and simulator must use English (US), matching the shipped interaction model. Once linked, the user does not say an account name, token, URL, or home identifier. Every authorized request selects the connected home associated with the service account.

For a separate test invocation, say **Alexa, ask home energy test for battery status** instead. Use the invocation configured in that skill's interaction model. Standard development-skill linking starts in the Alexa mobile app; the Developer Console simulator can exercise the linked skill but does not create the account link. The Alexa web chat is not a replacement for this flow. If the skill is already enabled but requests linking, open its settings in the Alexa app and choose **Link Account**.

Enable APL separately for this development skill to test [screen responses](../README.md#screen-responses). Speech and visual reports use the same authenticated household connection. An unlinked or unconfigured account receives setup instructions, never another home's screen data.

The provided realm issues five-minute access tokens and rotates refresh tokens. Alexa refreshes its token automatically while the identity session remains valid. The scaffold limits idle sessions to 30 days and total sessions to 90 days; after expiry or revocation, the user must link again. It does not request an unlimited offline grant. Portal sessions are short lived; signing in again is expected when their provider token expires.

## Disconnect, revoke, and delete

These actions have different effects:

- **Disconnect in the portal** removes the saved IGW connection for the signed-in service account. Future voice requests have no home to read. It does not delete the service account, revoke credentials at IGW or Cloudflare, or revoke Alexa's OAuth grant. A request already in flight can finish.
- **Disable/unlink the skill in the Alexa app** removes that Amazon account's link. It does not delete the saved connection in this service. Check that re-enabling the skill requires account linking again.
- **Revoke the service session or Alexa grant in Keycloak** prevents the provider from accepting the revoked session's tokens. The backend rechecks token status for subsequent reports. Test the revocation path against the installed provider; disabling the skill alone should not be treated as proof that every externally held access token was revoked.
- **Sign out of the portal** removes its browser session. It is not a global Keycloak logout and does not unlink Alexa or disconnect IGW. On a shared browser, also sign out of the identity provider through its account console.
- **Delete the service account** by disabling the Keycloak user, revoking its provider sessions/grants, erasing application data with the operator procedure below, then deleting the Keycloak user. Deleting a Keycloak user alone does not delete the application's encrypted connection or session records.

For operator-assisted account deletion, first verify the requester's identity through your support process and obtain the user's exact Keycloak **subject/user ID** before deleting that user. Disable the Keycloak user first, then revoke all of that user's provider sessions and grants so the user cannot sign in again while erasure is running. From the multi-household deployment directory, run:

```bash
docker compose exec portal energy-voice-admin delete-household
```

Enter the subject at the private prompt. The command uses the configured issuer, deletes that household's connection and every stored portal session in one transaction, and does not print the subject or any credentials. Connection saves check that their browser session still exists inside the same database transaction, so a save already waiting for IGW cannot recreate an erased connection. It leaves other households unchanged. Then delete the Keycloak user, and revoke that home's IGW/Cloudflare credentials if the owner requests it. Keep identifiers used to administer a deletion private.

Before accepting public users, the operator must choose and publish a backup-retention period and support process. Backups can retain older encrypted account records until they expire. Apply deletions again after restoring a backup before restarting public access; keep the minimal private deletion record needed for that recovery process, and expire it with the affected backups. This repository does not invent a retention period or claim to erase external IGW, Cloudflare, Amazon, or offline backup records.

To rotate a home's IGW or Cloudflare credential, create a replacement at its issuing service, save and verify the replacement through the portal, then revoke the old credential. The portal does not grant or revoke permissions in those external services. Never rotate credentials for a different household to test isolation.

The runtime cannot verify the authorization scope encoded in an opaque IGW token. Restrict that token to read-only at IGW itself. Portal sign-in gives a user control of their own saved connection; it does not make an administrative IGW token safe.

## Acceptance before public release

Complete these checks on a separate deployment with two consenting test households and separate service accounts. Keep evidence private and publish only sanitized outcomes.

1. Confirm production Keycloak starts with the imported realm, persists across restart, and requires S256 PKCE. Confirm exact redirect matching and that invalid client credentials cannot obtain tokens. Verify both OAuth clients have only their intended audience and scopes.
2. Link Household A to IGW A and Household B to IGW B. Use distinguishable synthetic reports or known private test data. All five reports for each account must come only from its own IGW, including after application and identity-provider restarts.
3. Exercise real Amazon authorization-code exchange, refresh-token rotation, token expiry, unlink/relink, and provider revocation. Portal tokens, expired tokens, unknown subjects, and missing `energy:read` must never return home data. An outage of Keycloak must fail closed.
4. Sign in as A in the portal and attempt stale/replayed login callbacks and connection submissions without the correct CSRF token. They must be rejected. A saved connection must survive failed validation of a replacement; B's connection must be unaffected by all of A's actions.
5. Confirm local/private IGW addresses, public names resolving to private addresses, redirects, invalid certificates, HTML login pages, wrong tokens, and invalid energy envelopes are rejected. Verify credentials are not returned in HTML, JSON errors, Alexa speech, or access logs.
6. Disconnect A and confirm A receives a setup prompt while B still gets B's reports. Relink A and restore only A's connection. Test account deletion against both identity and connection storage, including the backup-retention procedure.
7. Test the physical Echo on both authorized Amazon accounts. Verify microphone recognition, spoken reports, help, and stop/cancel. A passing mocked test suite, successful model build, or local health check does not prove Amazon linking or device operation.

Before submitting to the public store, also finish the operator's real signup/support flow, privacy policy, account deletion instructions, store assets, and Amazon review access. Do not substitute example business details or personal deployment identifiers into public repository fixtures. This implementation supplies the shared-service capability; it does not certify or publish the skill and does not enroll any live user automatically.
