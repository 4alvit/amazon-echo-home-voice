# Publish only the verified Alexa webhook

This procedure keeps the NAS Compose service on `127.0.0.1:8091` and uses a dedicated native Cloudflare Tunnel connector on that same NAS. It adds an exact hostname/path route and a hostname-specific `404` rule while preserving every existing ingress rule and top-level tunnel setting. The gateway's existing Cloudflare Access application and outbound service credentials remain unchanged.

Do not put a browser login, Access challenge, or service-token requirement on the Alexa hostname. Amazon cannot supply those credentials. `/alexa` instead verifies the official Amazon certificate chain, the SHA-256 request signature, the timestamp, and the exact configured skill ID before it can request IGW data. Keep the skill in Development for the owner's account and trusted testers. Skill ID validation does not authorize individual households; public distribution requires per-user authorization and isolated household credentials.

## Preconditions

1. Install the NAS service using [the Compose instructions](README.md#nas-only-alternative). Store the real skill ID in the protected `.env`, recreate the container, and verify that local `/health` reports `skill_configured: true`. Check the configured value against the skill in the Alexa Developer Console without printing the other environment values.
2. Identify the native connector's tunnel ID from its local configuration without printing its tunnel token. Confirm that the process shares the host network namespace and can reach `http://127.0.0.1:8091/health`. A connector in another machine or an isolated container cannot use this origin.
3. Read the target tunnel's connected clients through the Cloudflare API. There must be exactly one connector record, with the ID of the verified NAS connector. Multiple edge connections belonging to that record do not mean multiple connector hosts. Do not use a tunnel also served by the k3s VM or another host: requests could then reach the wrong loopback origin.
4. Choose a new dedicated hostname in the intended zone. Inspect the account's Access applications, including wildcard applications, and confirm that none requires a browser login or service token for this hostname. Do not weaken an existing IGW application to make Alexa reachable.

## Prepare a private plan without applying changes

The helper uses only Cloudflare `GET` operations. It verifies one expected connector, refuses an existing DNS record or hostname route, requires the existing final `404` catch-all, and refuses tunnel-wide Access enforcement. It inserts the two new rules before any matching wildcard or catch-all. Existing rules and other tunnel settings retain their values and order.

Use a read-scoped API token supplied through your secret manager as `CLOUDFLARE_API_TOKEN`. Credentials are read from the environment and never placed in arguments or output. For an existing global-key setup, `--auth key` uses `CLOUDFLARE_EMAIL` and `CLOUDFLARE_API_KEY` instead. API requests reject redirects and use a bounded timeout.

```bash
python3 scripts/prepare-tunnel-route.py \
  --account-id "$CF_ACCOUNT_ID" \
  --zone-id "$CF_ZONE_ID" \
  --tunnel-id "$CF_TUNNEL_ID" \
  --connector-id "$CF_CONNECTOR_ID" \
  --hostname "$ALEXA_HOSTNAME" \
  --state-dir "$PRIVATE_ROUTE_PLAN_DIR"
```

The output directory must not already exist. It is created with mode `700`; its files have mode `600`. Keep it outside Git: the backup contains other private hostnames and origins, even though it contains no API credentials. The four files are the original configuration/version, the full candidate configuration, the new DNS record, and the expected identifiers/version. The helper does not inspect your backend environment or Access applications; the preceding manual checks remain required.

The added rules have this form, immediately before any existing rule that could match the new hostname:

```json
[
  {"hostname": "alexa.example.com", "path": "^/alexa$", "service": "http://127.0.0.1:8091"},
  {"hostname": "alexa.example.com", "service": "http_status:404"}
]
```

The DNS candidate is a proxied CNAME to the selected tunnel's `cfargotunnel.com` target. All other hostname requests still use their existing routes.

## Apply the reviewed plan

The planning helper deliberately has no write mode. Apply the prepared JSON using the authorized Cloudflare API client only after reviewing the exact new public hostname, path and origin.

1. Immediately before writing, fetch the tunnel configuration again and require both its version and its complete `config` to equal `before.json`. Recheck the one expected connector, absent DNS hostname, and configured skill health. If anything changed, stop and prepare a new plan. Cloudflare's configuration update replaces the complete configuration; these checks detect earlier drift but are not an atomic compare-and-swap. Avoid concurrent tunnel edits during the operation.
2. Send `tunnel-candidate.json` as the body of `PUT /accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations`. Save its returned version privately and read the configuration back. It must equal the candidate, including all unchanged rules.
3. Send `dns-candidate.json` as the body of `POST /zones/{zone_id}/dns_records`. Save the returned record ID privately and verify its exact name, target and proxy setting. If a write response is lost or times out, read the current state before retrying; a timeout does not establish that a write failed.
4. Record the before/after versions, DNS record ID and verification results alongside the private backup. Never run a Terraform resource that manages only the new Alexa ingress against this shared tunnel; a partial replacement would delete unrelated routes.

## Verify and configure Alexa

Probe the public hostname without credentials:

```bash
curl --silent --show-error --output /dev/null --write-out '%{http_code}\n' \
  -H 'Content-Type: application/json' -d '{}' "https://$ALEXA_HOSTNAME/alexa"
curl --silent --show-error --output /dev/null --write-out '%{http_code}\n' \
  "https://$ALEXA_HOSTNAME/health"
curl --silent --show-error --output /dev/null --write-out '%{http_code}\n' \
  "https://$ALEXA_HOSTNAME/"
```

Expected results are `400`, `404`, `404`. The unsigned rejection body is the generic `Invalid Alexa request`; it must not contain configuration, credentials or gateway responses. `/alexa/` must also return `404`. TLS must validate without `--insecure`.

Set the Alexa Console HTTPS endpoint to `https://<your-hostname>/alexa`, select the certificate option matching the actual certificate, import/build the model and enable Development testing. A trusted wildcard certificate covering the hostname uses the Console's wildcard/subdomain option. Perform an Amazon-signed simulator request, then the [physical Echo checklist](../README.md#tests-and-physical-echo-checklist). Public negative probes prove rejection and routing, not a successful Alexa invocation or physical playback.

## Scoped rollback

Delete only the new DNS record, after checking its ID, hostname and tunnel target. Remove only the two exact inserted rules from the latest tunnel configuration, preserving any unrelated changes and the final catch-all. Restore the complete saved configuration only if the current configuration and version still equal the applied candidate; otherwise review the current configuration and remove the two rules without overwriting newer work. Keep the skill backend on loopback and retain IGW Access throughout.

References: [Cloudflare ingress matching and path regular expressions](https://developers.cloudflare.com/tunnel/advanced/local-management/configuration-file/), [routing DNS to a tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/), [Amazon HTTPS hosting requirements](https://developer.amazon.com/en-US/docs/alexa/custom-skills/host-a-custom-skill-as-a-web-service.html).
