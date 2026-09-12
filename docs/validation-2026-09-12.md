# Validation snapshot: September 12, 2026

The backend is installed privately on Synology. The Alexa custom skill is not registered or enabled yet; physical Echo recognition and playback have not been tested.

## Code and build checks

- All 18 local unit/security tests passed with the webhook extra installed. They include real SHA-256 verification, body-tamper rejection, expired-request rejection, and Amazon certificate URL restrictions.
- Package installation, worker preload, Python compilation, all five intent/model mappings, deployment shell syntax, Kubernetes and Compose YAML parsing, actionlint, and whitespace checks passed.
- GitHub CI passed for Python 3.11, 3.12, and 3.13 and for the Linux Docker webhook build/tests before this documentation-only update.
- The Synology Linux Docker build succeeded with the real ASK certificate verifier, including the pinned upstream oscrypto OpenSSL parsing fix.

Reviewed image: `igw-alexa:voice-review`, ID `sha256:2bc90d9a1cb9df15b2fef1db74e48dd3ee2ccb2c9691845c4b0466f1762dafb0`. Subsequent changes added deployment manifests and documentation without changing the image's runtime source.

## Installed backend

The dedicated Compose deployment is under `/volume1/docker/igw-alexa`, with container `igw-alexa` listening only on `127.0.0.1:8091`. Its protected environment is mode `600`; credentials were transferred through encrypted stdin and were not printed or committed. `ASK_SKILL_ID` remains empty.

Verified:

- Container running and healthy; non-root user `10001:10001`; read-only root filesystem.
- `GET /health` returned `200` with `{"status":"ok","skill_configured":false}`.
- Unsigned `POST /alexa` returned `400`.
- No public hostname route or Cloudflare policy was added for this backend.

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

Complete Alexa developer registration, create the custom skill, import/build its model, configure its real skill ID and a signature-verified HTTPS endpoint, and enable Development testing. Then run the simulator and physical Echo checklist in the main README. Backend installation and authenticated gateway reads alone do not establish that Alexa can invoke or speak the skill.
