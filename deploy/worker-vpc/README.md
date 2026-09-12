# Alexa relay through Workers VPC

This optional relay exposes `POST /alexa` on a `workers.dev` URL and forwards the
original signed request through a VPC Service to the existing NAS webhook.
The NAS retains its Amazon certificate, signature, timestamp, and skill ID
verification. The Worker holds no credentials, does not access IGW directly,
and does not format energy reports.

The private hop avoids the public hostname affected by zone Bot Fight Mode.
It does not disable Bot Fight Mode or depend on a User-Agent exception. This
directory is a deployment template; local tests do not prove a deployed VPC
connection or a successful Alexa invocation.

## Request and response limits

- Only `POST /alexa` without a query string is accepted. All other routes return
  HTTP 404, including `/health`.
- The request body is limited to 32 KiB while reading, even without
  `Content-Length`. Declared lengths must match the received bytes.
- Only `Content-Type`, `Signature-256`, and `SignatureCertChainUrl` are forwarded.
  Missing or oversized signature headers fail early. Header presence alone
  does not authenticate a request; the NAS must verify the signature.
- The upstream request uses the fixed URL `http://alexa-backend/alexa` and an
  `ArrayBuffer` body. Workers generates its actual `Content-Length`; the NAS
  WSGI application requires this header. The JSON is never reserialized. See
  [Workers Content-Length behavior](https://developers.cloudflare.com/workers/runtime-apis/request/#set-the-content-length-header).
- The VPC Service configuration fixes the real destination host and port.
  `alexa-backend` supplies the HTTP Host header; it is not a DNS destination.
- Redirects are not followed. HTTP 200 and HTTP 400–599 JSON object responses
  are passed back with their original bytes and status. Other responses,
  invalid JSON, and responses larger than 16 KiB produce a generic HTTP 502.
- One six-second deadline covers the client body, backend request, and response
  body. Cancellation is signalled to the backend. Keep the backend certificate
  and IGW timeouts within this budget; the defaults are two and three seconds.
- Only `Content-Type: application/json` and `Cache-Control: no-store` are
  returned. The code does not log request bodies, signatures, or responses.

## Prerequisites

[Workers VPC](https://developers.cloudflare.com/workers-vpc/configuration/vpc-services/)
is currently beta and available without an additional VPC charge across Workers
plans. This deployment must remain on **Workers Free**: do not add a payment
method, start a paid subscription, enable a paid service, or upgrade the account.
If a required feature stops being free, stop and disable or replace that feature
with an approved free alternative. Exceeding a quota is not authorization to
upgrade. See [Workers VPC beta pricing](https://developers.cloudflare.com/workers-vpc/reference/pricing/).

Workers Free currently allows 100,000 requests per day, shared across the
account's Workers and reset at midnight UTC, with 10 ms of CPU time per request.
Waiting for the NAS response is not CPU time. The relay runs on requests; it is
not a continuously running billed server. These are daily limits, not a monthly
pool. Quota exhaustion can make the endpoint unavailable. Check the current
[Free plan limits](https://developers.cloudflare.com/workers/platform/limits/)
before deployment. The account also needs permission to create a VPC Service
and bind it to a Worker.

The [tunnel requirements](https://developers.cloudflare.com/workers-vpc/configuration/tunnel/)
include cloudflared version 2025.7.0 or later, QUIC transport, and outbound UDP
port 7844. Inspect every connected client of the chosen tunnel: require exactly
one connector record, matching the verified NAS connector. Multiple edge
connections belonging to that one record are expected. Do not use a tunnel
shared by connectors on other hosts: loopback requests could reach the wrong
host. Recheck this condition before publishing and whenever adding connectors.

The connector must reach the NAS webhook. Register an HTTP VPC Service
with only the webhook's private host and HTTP port. Use loopback only when the
connector shares the webhook host's network namespace; container loopback may
refer to a different host. Do not register the public Cloudflare hostname or a
binding to the entire private network.

The Worker is publicly reachable, so the NAS verifier must remain enabled.
Ensure the selected NAS port runs this project's webhook, not an unsigned test
handler or another service. No Cloudflare Access login should protect the public
Worker endpoint, because Amazon cannot complete an interactive login.

## Configure and publish

1. Create a dedicated HTTP VPC Service for the NAS webhook and record its service
   ID privately. Confirm its tunnel, sole NAS connector record, exact private
   host, and port.
2. Copy `wrangler.example.jsonc` to `wrangler.jsonc` in this directory. Set the
   chosen Worker name and replace the service ID placeholder. Keep the binding
   name `ALEXA_BACKEND`. The local configuration is ignored by Git.
3. Select the account's `workers.dev` subdomain if one does not exist. Check the
   resulting public endpoint before publishing. The example leaves preview URLs
   and Worker observability disabled and does not add routes to existing zones.
   Before creating an account namespace, inspect all existing Workers: an
   existing enabled `workers.dev` or preview setting could expose another
   project. Require their existing public scope to remain unchanged. Creating
   this namespace does not transfer or rename your existing custom domain.
4. With a current authenticated Wrangler installation, run these commands from
   this directory after reviewing the configuration:

   ```sh
   wrangler deploy --config wrangler.jsonc --dry-run
   wrangler deploy --config wrangler.jsonc
   ```

5. Set the Alexa skill's HTTPS endpoint to the published
   `https://<worker>.<account-subdomain>.workers.dev/alexa`, with the applicable
   trusted certificate setting, and save it. Retain the old endpoint privately
   for rollback. No invocation-model change is required.

Cloudflare recommends a custom domain for business-critical production Workers;
`workers.dev` is intended for personal or hobby projects. A custom domain would
again introduce that zone's security settings. See
[workers.dev](https://developers.cloudflare.com/workers/configuration/routing/workers-dev/).

## Validation

Run the dependency-free local tests with Node.js 22 or later from the repository
root:

```sh
node --test deploy/worker-vpc/relay.test.mjs
```

The tests mock the VPC binding. They verify raw bytes, header isolation, fixed
destination, NAS rejection propagation, size limits, redirects, malformed
responses, and the six-second deadline. They do not exercise Cloudflare's
transport, HTTP `Content-Length` generation, or Amazon's signing service.

After publication, verify the public relay rejects missing signatures and that a
request with invalid signatures reaches the NAS and remains HTTP 400. Use a real
Alexa simulator request to confirm a signed request reaches the NAS with the
correct content length and returns the expected speech. Check all five reports,
then verify invocation on the intended Echo device separately. Confirm zone Bot
Fight Mode remains enabled. Keep any diagnostic logging limited to HTTP status,
byte counts, and signature-header presence; never log the payload or signature.

To roll back, select a previously verified working Alexa endpoint before
disabling or removing this Worker. A retained direct endpoint that is still
challenged by Bot Fight Mode is not a working automatic fallback; its non-beta
configuration requires separate review and approval. Do not weaken shared zone
protection as an incidental rollback step. Remove the dedicated VPC Service
only after confirming nothing else uses it. Keep the NAS verifier enabled.
