# Validation snapshot: September 12, 2026

The backend is installed on Synology. A personal Alexa custom skill has been created and its exact ID configured. A dedicated HTTPS route exposes only the signature-verified `/alexa` endpoint, but Cloudflare Bot Fight Mode currently challenges Amazon's requests. Successful Alexa invocation and physical Echo playback remain unverified.

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

## Remaining end-to-end checks

The model was imported and built in the Alexa Console with zero errors. Amazon added `AMAZON.NavigateHomeIntent` automatically and displayed one warning; unsupported intents use the adapter's existing fallback. The HTTPS endpoint was saved using the wildcard-certificate option matching the verified certificate.

Development testing is enabled. The initial simulator attempt reported that the skill was unsupported on that device, with no Skill I/O shown. An explicit invocation and a manual JSON test then reported an invalid skill response; the user observed HTTP `403`. The confirmed edge-policy cause is recorded below. Resolve that incompatibility before repeating the simulator and physical Echo checklist. Backend installation and authenticated gateway reads alone do not establish that Alexa can invoke or speak the skill.

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

Temporary Gunicorn access logging was enabled with only method, path and response status, without request bodies, headers, IP addresses or credentials. The observed log window contained successful local health requests and no Alexa POST reaching the application. Remove this temporary logging configuration after diagnosis and final verification.

Cloudflare documents that standard Bot Fight Mode cannot be skipped through WAF custom rules or Page Rules; Super Bot Fight Mode supports scoped exceptions. The current account has one zone and no already configured Workers subdomain, so no alternative endpoint was assumed available or published. A separately reviewed deployment choice is required; the shared zone's protection was not disabled. See [Cloudflare's documented limitations](https://developers.cloudflare.com/bots/get-started/bot-fight-mode/) and [false-positive guidance](https://developers.cloudflare.com/bots/troubleshooting/false-positives/).

## Prepared Workers VPC alternative

The optional relay and its 40 dependency-free Node tests are implemented and reviewed. All 40 tests pass locally. CI runs them with Node 22. They cover unchanged signed bytes, isolated headers, the fixed VPC destination, bounded request/response bodies, rejected redirects and the complete six-second deadline. They mock the VPC binding and do not establish live connectivity.

Read-only prerequisites were checked: the native NAS connector runs cloudflared 2026.7.3, uses the host network namespace, and exposes four active QUIC connections through its local metrics. The account's VPC service listing API is available and currently empty. A private candidate fixes the VPC service to NAS loopback port 8091 through the already verified single connector. No binding to the entire private network is proposed.

The candidate uses a new `workers.dev` endpoint with preview URLs and observability disabled. Publication and changing the skill endpoint are pending explicit approval of that new address. No Workers subdomain, VPC Service or Worker has been created, and no paid plan or zone-wide protection change is part of this proposal. VPC is beta and normal Workers plan limits apply. The live private hop, Amazon-signed response and physical Echo still need verification after deployment.
