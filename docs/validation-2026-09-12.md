# Validation snapshot: September 12, 2026

The backend is installed on Synology. A personal Alexa custom skill has been created and its exact ID configured. A dedicated HTTPS route now exposes only the signature-verified `/alexa` endpoint. Physical Echo recognition and playback have not been tested.

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

Development testing is enabled. Complete a successful simulator invocation and the physical Echo checklist in the main README. The initial simulator attempt reported that the skill was unsupported on that device, with no Skill I/O shown; this does not establish whether the backend received a request. Backend installation and authenticated gateway reads alone do not establish that Alexa can invoke or speak the skill.

## Dedicated HTTPS route

After explicit approval of the exact public endpoint, the existing native Synology tunnel was verified to have one connector record on the NAS host network. Its four edge connections belong to that one connector. The k3s tunnel is a different tunnel and is not used for this loopback origin.

The deployment saved the original version and full configuration privately, checked them again before applying, and added two exact rules ahead of the existing final catch-all. All 12 previous ingress rules and the other tunnel settings were preserved unchanged; there are now 14 rules. A new proxied CNAME points only the dedicated Alexa hostname at this tunnel. The existing IGW Access application and outbound credentials were not changed. The [generic procedure and read-only planning helper](../deploy/tunnel-routing.md) reproduce this route structure without storing site identifiers or credentials in Git.

Public checks with validated HTTPS:

- Unsigned `POST /alexa`: `400`, generic `{"error":"Invalid Alexa request"}`.
- `GET /health`: `404`.
- `GET /`: `404`.
- `POST /alexa/`: `404`.

The certificate is issued by Google Trust Services and covers the dedicated subdomain through its parent-zone wildcard. These checks establish routing and unsigned rejection only; Amazon-signed and physical Echo results must be recorded separately.
