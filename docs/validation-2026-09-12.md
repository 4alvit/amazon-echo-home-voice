# Validation snapshot: September 12, 2026

The backend is installed on Synology. The personal Alexa custom skill is configured and all five report intents returned the expected English speech responses in the real Amazon simulator through the approved Workers VPC relay. The NAS retains Amazon signature verification. Physical Echo recognition and playback remain unverified. The original zone's Bot Fight Mode remains enabled.

## Code and build checks

- The original 18 local unit/security tests passed with the webhook extra installed. They include real SHA-256 verification, body-tamper rejection, expired-request rejection, and Amazon certificate URL restrictions.
- Package installation, worker preload, Python compilation, all five intent/model mappings, deployment shell syntax, Kubernetes and Compose YAML parsing, actionlint, and whitespace checks passed.
- GitHub CI passed for Python 3.11, 3.12, and 3.13 and for the Linux Docker webhook build/tests before this documentation-only update.
- The Synology Linux Docker build succeeded with the real ASK certificate verifier, including the pinned upstream oscrypto OpenSSL parsing fix.
- After adding the route planner, all 22 tests passed in a separate read-only, network-disabled Synology container from the same reviewed image. This includes all three real signature/security tests and four new route-preservation tests. Local dependency-free tests passed with those three optional signature tests skipped; actionlint and whitespace checks passed.

Reviewed image: `igw-alexa:voice-review`, ID `sha256:2bc90d9a1cb9df15b2fef1db74e48dd3ee2ccb2c9691845c4b0466f1762dafb0`. Subsequent changes added deployment manifests and documentation without changing the image's runtime source.

## Installed backend

The dedicated Compose deployment is under `/volume1/docker/igw-alexa`, with container `igw-alexa` listening only on `127.0.0.1:8091`. Its protected environment is mode `600`; credentials were transferred through encrypted stdin and were not printed or committed. After creating the skill, only `ASK_SKILL_ID` was updated and the same reviewed image was restarted.

Verified:

- Container running and healthy; non-root user `10001:10001`; read-only root filesystem.
- Initial `GET /health` returned `200` with `{"status":"ok","skill_configured":false}`; after configuring the exact created skill ID it returned `{"status":"ok","skill_configured":true}`.
- Unsigned `POST /alexa` returned `400`.
- The host port remains restricted to NAS loopback. The public route described below does not expose `/health`.

The NAS kernel does not support Docker CPU CFS quotas, so its manifest uses relative CPU scheduling weight rather than a hard CPU cap. Docker also reports that the PID cgroup controller is unavailable and discards the requested PID limit. The deployment documentation records these limitations; the requested PID limit must not be described as enforced on this host.

## Live gateway reads from the installed container

All five `energy-voice` CLI commands completed with exit status `0` and report status `fresh`, using the approved read-only IGW token and the paired Cloudflare service credentials through the protected HTTPS endpoint:

- `battery`: Battery charge is 67.3 percent.
- `solar`: Solar power is 1.6 kilowatts.
- `solar_today`: Solar generation today is 1.99 kilowatt hours.
- `alarms`: No active alarms in the monitored sources.
- `status`: Battery charge is 67.4 percent. Solar power is 1.59 kilowatts. Solar generation today is 1.99 kilowatt hours. No active alarms in the monitored sources.

These are separate live reads, so small numerical differences between the individual and combined reports are expected. They are a dated verification sample, not fixed expected values. The alarm statement is limited to the gateway's monitored sources.

## Alexa Console and real signed requests

The model was imported and built in the Alexa Console with zero errors. Amazon added `AMAZON.NavigateHomeIntent` automatically and displayed one warning; unsupported intents use the adapter's existing fallback. The HTTPS endpoint was saved using the wildcard-certificate option matching the verified certificate.

Development testing is enabled. The initial direct endpoint returned HTTP `403` to Amazon because of the confirmed edge-policy cause below. After deployment of the approved VPC relay, the skill's HTTPS endpoint was changed and its saved value verified by reloading the Alexa Console. The selected wildcard certificate option matches the new certificate's verified subject alternative names.

All five real simulator invocations succeeded through Amazon's signing service, the Worker, the private VPC binding, the NAS verifier and the authenticated live gateway:

- Battery status returned the gateway's battery charge report.
- Solar power returned the current power report in kilowatts.
- Solar energy today returned the daily generation report in kilowatt hours.
- Alarm status returned the monitored-source alarm summary, including its scope wording.
- System status returned the combined battery, power, daily energy and alarm report.

These checks used actual live gateway data, not a local event fixture or mocked VPC binding. The NAS access log confirmed HTTP `200` for the real requests. Numeric values changed between sequential requests as expected. The remaining physical check is recognition and playback on the intended Echo, signed into the developer account with the skill in Development.

The explicit launch phrase `open home energy skill` also succeeded in the Alexa+ simulator and returned the adapter's welcome and question prompt. The model's invocation name remains `home energy`; adding the word `skill` disambiguates this launch phrase in the tested client. Within that session, `help` returned the expected English help prompt and `stop` returned `Goodbye.` successfully.

## Dedicated HTTPS route

After explicit approval of the exact public endpoint, the existing native Synology tunnel was verified to have one connector record on the NAS host network. Its four edge connections belong to that one connector. The k3s tunnel is a different tunnel and is not used for this loopback origin.

The deployment saved the original version and full configuration privately, checked them again before applying, and added two exact rules ahead of the existing final catch-all. All 12 previous ingress rules and the other tunnel settings were preserved unchanged; there are now 14 rules. A new proxied CNAME points only the dedicated Alexa hostname at this tunnel. The existing IGW Access application and outbound credentials were not changed. The [generic procedure and read-only planning helper](../deploy/tunnel-routing.md) reproduce this route structure without storing site identifiers or credentials in Git.

Public checks with validated HTTPS:

- Unsigned `POST /alexa`: `400`, generic `{"error":"Invalid Alexa request"}`.
- `GET /health`: `404`.
- `GET /`: `404`.
- `POST /alexa/`: `404`.

The certificate is issued by Google Trust Services and covers the dedicated subdomain through its parent-zone wildcard. These checks establish routing and unsigned rejection only; Amazon-signed and physical Echo results must be recorded separately.

## Confirmed Amazon request rejection

Cloudflare Security Events for the dedicated hostname and `/alexa` during the 17:48–17:49 UTC tests recorded `action: managed_challenge`, `source: botFight`, and `ruleId: bot_fight_mode`. The requesting client identified itself as Apache HttpClient on Java 17. The zone uses the Free plan and its bot-management API reports `fight_mode: true`. This is direct evidence of Bot Fight Mode blocking the voice request; it is not an inference from the HTTP status or a Browser Integrity Check error.

The existing home-IP allowlist skips remaining custom rules for listed addresses. The separate home-only block covers three other explicitly named hostnames; it does not cover the Alexa hostname. A zone-wide geographical rule can still affect other caller locations, while the current US/Korea rule skips remaining custom rules. None of these rules skips standard Bot Fight Mode. No Access application matches the Alexa hostname. Existing custom rules, bot settings and IGW Access were not modified.

Temporary Gunicorn access logging was enabled with only method, path and response status, without request bodies, headers, IP addresses or credentials. The initial blocked window contained successful local health requests and no Alexa POST reaching the application. The later VPC test window confirmed rejected probe requests and successful real Amazon requests. After the completed report and lifecycle tests, the temporary argument was removed and the original logging configuration restored.

Cloudflare documents that standard Bot Fight Mode cannot be skipped through WAF custom rules or Page Rules; Super Bot Fight Mode supports scoped exceptions but would require a different plan. The account had one zone and no Workers subdomain when investigated. The separately approved free VPC deployment below addresses the incompatibility without disabling the shared zone's protection. See [Cloudflare's documented limitations](https://developers.cloudflare.com/bots/get-started/bot-fight-mode/) and [false-positive guidance](https://developers.cloudflare.com/bots/troubleshooting/false-positives/).

## Deployed Workers VPC relay

The optional relay and its 40 dependency-free Node tests are implemented and reviewed. All 40 tests pass locally. CI runs them with Node 22. They cover unchanged signed bytes, isolated headers, the fixed VPC destination, bounded request/response bodies, rejected redirects and the complete six-second deadline. They mock the VPC binding and do not establish live connectivity.

Read-only prerequisites were checked: the native NAS connector runs cloudflared 2026.7.3, uses the host network namespace, and exposes four active QUIC connections through its local metrics. The deployed VPC service fixes its destination to NAS loopback HTTP port 8091 through the already verified single connector; no HTTPS port or alternate host is configured. The Worker has only this VPC Service binding, with no binding to the entire private network.

After explicit approval of the exact address, the account namespace, private VPC Service and Worker were created. The production `workers.dev` endpoint was enabled only after checking its fixed binding; preview URLs, observability and Logpush are disabled. The existing unrelated Worker retained its disabled `workers.dev`/preview settings and unchanged custom domain. Tunnel ingress, zone bot settings and IGW Access were unchanged. No payment method, paid service or subscription was added.

TLS validated using the system trust store. The certificate is issued by Let's Encrypt and its wildcard covers the relay's account subdomain. Public probes returned `400` for missing signatures and `404` for `/`, `/health` and `/alexa/`. A request containing dummy signature headers and the JSON body `{}` returned `400` and appeared as `POST /alexa 400` in the sanitized NAS access log, proving the private binding reached the NAS verifier. A later real Amazon simulator request appeared as `POST /alexa 200`.

The original direct hostname and its two ingress rules are retained for a separately reviewed non-beta fallback. It remains subject to the original zone protections and is not currently a working automatic fallback for Alexa. No sensitivity change, zone-wide Bot Fight Mode disablement or paid-plan upgrade was applied.

## Free account constraint and quota snapshot

The operator explicitly requires the Cloudflare account to remain Free, without adding payment methods, paid subscriptions or paid services. A quota or future paid requirement must result in stopping, disabling or replacing the affected feature with an approved free alternative, never an automatic upgrade. Workers VPC is an open beta currently available without an additional VPC charge; its future availability and terms can change.

Workers Free currently shares 100,000 requests per UTC day across account Workers, resetting at midnight UTC, and allows 10 ms of CPU time per request; network waiting is excluded from CPU time. This is not a monthly pool or a continuously billed server. See [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) and [VPC beta pricing](https://developers.cloudflare.com/workers-vpc/reference/pricing/).

The aggregate-only account query at 18:14:34 UTC reported 49 requests for the complete September 11 UTC day and 20 for September 12 through that timestamp, with zero reported errors. These represent 0.049% and 0.020% of the daily Free request allowance. Only the existing Worker had appeared in that analytics window; the newly deployed relay was not yet reported. Analytics lag must not be interpreted as zero new-relay usage. No request logs or bodies were collected for this quota check.
