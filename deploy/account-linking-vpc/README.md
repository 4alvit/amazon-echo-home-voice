# Account linking through Workers VPC

This optional template exposes the existing multi-household connection portal and
its Keycloak `home-energy` realm through two separate Workers. Each Worker uses
one `BACKEND` VPC Service binding with a fixed destination. It complements the
[signed Alexa webhook relay](../worker-vpc/README.md); it does not handle
`POST /alexa`, verify Alexa signatures, or read gateway telemetry.

The relay contains no account IDs, service IDs, private addresses, credentials,
or deployment records. Its tests use synthetic requests and mocked bindings.
Adding this directory does not create a Worker or change a running deployment.

## Public boundary

Set `ROLE` to `portal` or `auth`, and `PUBLIC_HOST` to that Worker's exact
`<worker>.<account-subdomain>.workers.dev` host. HTTPS and an exact host match are
required. Other roles and host formats fail closed.

- The portal accepts `GET /`, `/login`, and `/callback`, and `POST /connection`,
  `/disconnect`, and `/logout`. Only `/callback` accepts query parameters.
- The auth Worker accepts the realm's authorization, token, introspection,
  revocation, logout, certificate, discovery, and login-action endpoints, plus
  `/resources/` login assets. Each path has a method allowlist. Other realms,
  administration, health, metrics, and ambiguous encoded paths are rejected.
- Form bodies remain byte-for-byte unchanged and use a known-length
  `ArrayBuffer` for the backend. Request bodies are capped at 16 KiB for the
  portal and 32 KiB for auth; compressed or mismatched bodies are rejected.
- Only necessary browser headers are forwarded. Client-supplied forwarding,
  Cloudflare Access, tracing, and unrelated authorization headers are dropped.
  `CF-Connecting-IP` supplies the validated edge address; a direct deployment
  must keep Cloudflare as the trusted ingress. Basic authorization is accepted
  only on the token, introspection, and revocation endpoints.
- Redirects are returned to the browser without being followed. Keycloak still
  enforces the registered redirect URI allowlist. Insecure redirects and
  redirects containing credentials or non-default ports are rejected.
- Separate `Set-Cookie` headers and selected browser security headers survive.
  Responses are marked `no-store`. Upstream HTTP 5xx bodies are canceled and their responses
  replaced with a generic HTTP 502 response. Relay-generated errors
  also mask backend details; normal browser and OAuth 4xx bodies pass through.
- One 15-second budget covers request reading, backend fetch, and response
  streaming. Dynamic responses are capped at 256 KiB and login assets at 2 MiB.
  Deadline expiry or client cancellation cancels the upstream stream. Static
  assets stream without retaining the entire response in the JavaScript heap.

The portal continues to enforce sessions, CSRF, and the OAuth callback checks.
Keycloak continues to authenticate users and confidential clients. This relay's
path and size limits do not replace those checks or rate limiting. Follow the
[multi-household proxy requirements](../multi-household/README.md#publish-only-the-intended-https-surfaces)
when exposing a deployment.

## Local configuration

Use the existing [VPC prerequisites and connector scope requirements](../worker-vpc/README.md#prerequisites).
Configure a separate, narrowly scoped VPC Service for each backend; do not use a
binding to an entire private network or share an unrestricted backend between
roles. The `http://PUBLIC_HOST` URL passed to the binding preserves the public
HTTP host for the application; the VPC Service configuration fixes the actual
private destination.

1. Copy `wrangler.portal.example.jsonc` to `wrangler.portal.jsonc` and
   `wrangler.auth.example.jsonc` to `wrangler.auth.jsonc` in this directory.
2. Replace every `REPLACE_WITH_...` value locally. Set each Worker name and
   `PUBLIC_HOST` consistently, and set only that role's exact VPC Service ID.
   The supplied placeholders intentionally fail the host validation until
   configured. Preview URLs and Worker observability start disabled, and the
   examples add no custom-domain routes.
3. Keep both configured Wrangler files local with mode `0600`. They are ignored
   by Git, including alternative JSON/TOML Wrangler files and local `.dev.vars`
   files. Keep identity-provider secrets, private addresses, account/service
   IDs, deployment responses, and operator records under ignored `private/` or
   `.local-secrets/`; never copy them into examples or tests.
4. Configure the portal's public callback and the realm's public issuer and
   registered redirect URIs consistently. Keep Keycloak administration and
   management endpoints private. Changing these values or publishing a Worker
   is a deployment operation separate from running the local tests.

The examples contain placeholders only. Do not replace their contents with a
live configuration when committing changes.

## Validation

From the repository root, run:

```sh
bash scripts/ci.sh worker
```

This runs both the signed Alexa relay tests and the account-linking relay tests.
The account-linking tests cover route and method isolation, opaque form bytes,
header isolation, cookie separation, redirect handling, response streaming,
size limits, stalled streams, and cancellation. CI uses Node.js 22.

These synthetic tests do not prove a live VPC connection, Cloudflare transport,
identity-provider configuration, or a successful browser/Alexa login. Validate
those separately after configuring a deployment, without logging form bodies,
authorization headers, cookies, callback query strings, or credentials.
