# Validation summary — 2026-09-12

This summary records completed checks without publishing deployment addresses, account or device identifiers, runtime paths, live telemetry, access-policy details, or account usage. Examples elsewhere in the repository are configuration templates; they are not deployed endpoints.

## Automated checks

- Python tests passed for all five report intents, lifecycle requests, application IDs, timestamps, unsupported requests, authenticated gateway access, redirects, response limits, freshness, and malformed gateway responses.
- Optional webhook tests passed with the real certificate verification dependency, including valid SHA-256 signatures, tamper rejection, timestamp rejection, and Amazon certificate URL restrictions.
- The container build successfully loaded the ASK model and certificate verification stack.
- Worker relay tests passed for unchanged signed request bytes, isolated headers, a fixed VPC destination, request and response bounds, rejected redirects, and the complete request deadline. These tests mock the VPC binding; they do not establish live connectivity.
- Tunnel planning tests passed for preserving existing ingress rules and adding the exact Alexa route and hostname catch-all. The planning helper performs no remote writes.

These checks establish the behavior exercised by the tests. They do not certify arbitrary infrastructure or prove microphone recognition and playback on a physical Echo.

## Backend installation and gateway reads

A self-hosted container was installed with a protected runtime environment file, a dedicated read-scoped IGW token, the configured skill ID, and outbound Cloudflare Access service credentials. The container used a non-root user, a read-only filesystem, dropped capabilities, bounded memory, and a loopback-only host binding. Host cgroup support must be checked independently before relying on optional resource controls.

Health reported the running process and configured skill. Unsigned requests were rejected. Authenticated gateway reads succeeded for battery charge, current solar power, daily solar generation, monitored alarms, and combined system status. The returned English reports matched the gateway data used for those requests. Live numeric readings are intentionally omitted.

The alarm report describes only the gateway's configured monitored sources. No live equipment was interrupted and no device control command was issued for these voice checks. Stale, disconnected, and unavailable cases were exercised in controlled tests rather than by disrupting the installation.

## Direct HTTPS route

The connector's placement was checked before configuring a loopback origin. Existing tunnel configuration was backed up privately and preserved when adding the exact `/alexa` route and the hostname's catch-all rejection. Gateway Access protection remained separate from inbound Alexa routing.

Public probes with validated HTTPS returned:

- `400` for an unsigned `POST /alexa`.
- `404` for `GET /health`, `GET /`, and `POST /alexa/`.

A real Amazon invocation through the direct hostname was rejected with HTTP `403`. Cloudflare security events identified a Bot Fight Mode managed challenge, rather than an application rejection. Standard Bot Fight Mode cannot be skipped with WAF custom rules or Page Rules; see [Cloudflare's documented limitations](https://developers.cloudflare.com/bots/get-started/bot-fight-mode/).

This establishes why a successful local or unsigned routing probe is insufficient. The direct route remains a possible Plan B after a deliberate review of the zone-wide Bot Fight Mode setting and successful real Amazon tests. No Bot Fight Mode disablement was applied as part of this validation.

## Workers VPC relay and real Amazon requests

The relay's private HTTP VPC Service was checked against the intended connector, destination host, and webhook port. The Worker used only that service binding, preserved the signed request bytes, and retained the backend's certificate, signature, timestamp, and exact skill-ID verification. It did not receive gateway credentials or a binding to the entire private network.

Validated public TLS probes rejected unsigned requests and unsupported paths. A probe with dummy signature headers reached the backend and was rejected there, establishing private transport without bypassing authentication. Actual Amazon requests subsequently reached the verifier and returned HTTP `200`.

The skill model was imported and built in the Developer Console. The HTTPS endpoint and certificate option were saved and checked again after reloading the Console. With Development testing enabled, all five real simulator invocations succeeded through Amazon's signing service, the relay, the private binding, the backend verifier, and the authenticated gateway:

- Battery status returned the gateway's battery charge report.
- Solar power returned the current power report in kilowatts.
- Solar energy today returned the daily generation report in kilowatt hours.
- Alarm status returned the monitored-source alarm summary with its scope wording.
- System status returned the combined report.

The explicit launch phrase `open home energy skill` succeeded in the Alexa+ simulator and returned the welcome prompt. The invocation name remains `home energy`; the additional word `skill` disambiguated the launch in the tested client. `help` returned the English help prompt, and `stop` returned `Goodbye.`

Temporary diagnostic logging recorded only method, path, and response status. It omitted request bodies, headers, IP addresses, and credentials; the original logging configuration was restored after validation. Zone security settings, unrelated services, and outbound gateway protection were preserved.

## Free-only deployment policy

The tested route used Workers Free and the currently free Workers VPC open beta. No payment method, paid subscription, paid service, or plan upgrade was added. Beta availability and terms can change; a future paid requirement must lead to disabling or replacing the affected feature, not an automatic upgrade.

Workers Free currently shares a daily request allowance across the account and limits CPU time per request. Network waiting does not consume CPU time, and an idle relay is not a continuously running billed server. Check [current Workers limits](https://developers.cloudflare.com/workers/platform/limits/), [Workers pricing](https://developers.cloudflare.com/workers/platform/pricing/), and [VPC beta pricing](https://developers.cloudflare.com/workers-vpc/reference/pricing/) before deployment. Account usage and other application inventories are private and are not included here.

## Still unverified

Physical Echo microphone recognition and playback remain a separate check. Use an Echo signed into the intended developer or trusted test account, with the skill in Development and the device set to English (US). Simulator success does not establish that physical result.

Plan B is documented as an operator-controlled alternative, not an automatic or already validated fallback. After changing the route or a zone policy, repeat the real Amazon simulator tests before relying on that route. Public distribution is also outside this validation and would require per-user authorization and household isolation.
