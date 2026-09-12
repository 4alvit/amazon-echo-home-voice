# Home Energy for Amazon Echo

A read-only **Alexa custom skill** for battery charge, current solar power, solar energy today, alarms, and system status. It reads centrally formatted English reports from `inverter-gateway` (IGW). Home Assistant is not required.

```text
Cerbo GX / Venus OS → IGW /v1/energy → this adapter → Alexa → Echo
```

IGW owns device selection, units, aggregation, freshness, and wording. The adapter makes one authenticated GET and copies `reports.<name>.text` into an Alexa `PlainText` response. It has no MQTT credentials, calculations, or write commands. This repository replaces earlier Home Assistant YAML examples.

## Included and still required

The repository includes an English (US) interaction model with invocation name **home energy**, a Python Lambda handler, a signature-verified self-hosted HTTPS webhook, a dependency-free smoke CLI, mocked tests, Docker Compose, and an optional AWS SAM template.

See the [dated installation and verification snapshot](docs/validation-2026-09-12.md) for completed checks and the remaining Alexa setup.

For a private installation on the home k3s cluster, see [deployment instructions](deploy/README.md) and [the ClusterIP workload](deploy/k3s.yaml). That path keeps the skill disabled until a real skill ID is configured.

You can install the backend while developer registration is unfinished by leaving `ASK_SKILL_ID` empty. Health then reports `skill_configured: false`, and every Alexa request is rejected until a real skill ID is supplied.

**Installing the backend does not create or enable an Alexa skill.** The Developer Console must contain a custom skill with this model and the installed endpoint; Development testing must be enabled. A physical Echo test is a separate step. No cloud accounts or credentials are included. This is an installable personal backend, not a publicly certified Alexa product or multi-household service.

Keep this skill in **Development**, limited to your own Amazon account and explicitly trusted test accounts. The application ID authenticates the skill, not the individual household or speaker. Do not publish it for public distribution with one shared gateway credential; first add per-user authorization/account linking and per-household isolation.

## Gateway contract

IGW must implement `GET /v1/energy` with a dedicated read-scoped token. Do not reuse its write/admin token. Required envelope:

```json
{
  "schema_version": 1,
  "generated_at": 1800000000,
  "mqtt_connected": true,
  "metrics": {"battery_soc": {}, "solar_power": {}, "solar_today": {}},
  "reports": {
    "battery": {"status": "fresh", "text": "Battery charge is 75 percent."},
    "solar": {"status": "fresh", "text": "Solar power is 2.4 kilowatts."},
    "solar_today": {"status": "fresh", "text": "Solar energy today is 12 kilowatt hours."},
    "alarms": {"status": "fresh", "text": "No active alarms in the monitored sources."},
    "status": {"status": "fresh", "text": "Battery charge is 75 percent. Solar power is 2.4 kilowatts. No active alarms in the monitored sources."}
  }
}
```

These are illustrative values; IGW supplies actual metric structures and current Unix `generated_at`. The adapter validates required metric names but does not consume metric internals. It validates all report statuses/text. Accepted statuses are `fresh`, `stale`, `unavailable`, and `unconfigured`. IGW also supplies non-fresh explanations so both voice platforms explain the same condition.

## Local installation and smoke check

Requires Python 3.11 or newer:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
cp .env.example .env
chmod 600 .env
```

Edit `.env` with the actual gateway URL, dedicated read token, and skill ID. If IGW uses Cloudflare Access, set both `CF_ACCESS_CLIENT_ID` and `CF_ACCESS_CLIENT_SECRET`. `.env` is ignored by Git. Never put credentials in examples, commits, or `samconfig.toml`.

The CLI reads environment variables and does **not** automatically source `.env`. Export values through your secret manager or enter secrets without putting them in shell history:

```bash
export IGW_URL='https://energy.example.com/v1/energy'
read -r -s -p 'IGW read token: ' IGW_READ_TOKEN
echo
export IGW_READ_TOKEN
.venv/bin/energy-voice status
.venv/bin/energy-voice battery
.venv/bin/energy-voice solar
.venv/bin/energy-voice solar_today
.venv/bin/energy-voice alarms
```

Export both Cloudflare values when needed. The CLI does not need `ASK_SKILL_ID`. Exit status is `0` for a fresh report, `2` for a validated non-fresh report, and `1` for a failed request or invalid contract/configuration. Stdout contains central speech; stderr contains status or a sanitized error category. This verifies the real gateway path, not the Echo microphone or Alexa endpoint.

## Self-hosted webhook: no AWS account needed

1. Create a custom skill in the [Alexa Developer Console](https://developer.amazon.com/alexa/console/ask), choose English (US) and your own backend. Copy its skill ID to `ASK_SKILL_ID` in `.env`.
2. Import `skill-package/interactionModels/custom/en-US.json` in the model JSON editor, save, and build.
3. Configure `.env`, then install the backend:

   ```bash
   docker compose up --build -d
   curl --fail http://127.0.0.1:8091/health
   ```

4. Publish only the exact `/alexa` path on a dedicated hostname through your trusted TLS reverse proxy or Cloudflare Tunnel to `http://127.0.0.1:8091`; other paths must return `404`. A connector on another host cannot reach this loopback port. For a dedicated native connector on the same NAS, use [the route planning and deployment procedure](deploy/tunnel-routing.md). If the tunnel runs in another container on the same Docker host, use a private Docker network and route to `http://alexa:8080` instead. Keep the host port on loopback.
5. Set the skill HTTPS endpoint to `https://voice.example.com/alexa` and choose the certificate option appropriate for your trusted certificate. **Inbound Alexa must not face a browser login, Access challenge, or service-token requirement.** Alexa does not send your Cloudflare credentials. Protect the outbound IGW endpoint separately.
6. Enable the Development testing stage and complete the simulator/Echo checklist below.

Inbound requests require Amazon's official ASK certificate-chain verification, SHA-256 signature over the original raw body, a 150-second timestamp tolerance, and the exact application ID in context and session. Missing configuration, wrong signatures/IDs, old requests, oversized bodies, or unknown request types fail closed. There is no verification-disable switch. See [Amazon HTTPS requirements](https://developer.amazon.com/en-US/docs/alexa/custom-skills/host-a-custom-skill-as-a-web-service.html) and [official Python verifier](https://github.com/alexa/alexa-skills-kit-sdk-for-python/tree/master/ask-sdk-webservice-support).

The webhook extra pins the upstream [oscrypto OpenSSL parsing fix](https://github.com/wbond/oscrypto/commit/d5f3437ed24257895ae1edd9e503cfb352e635a8) because its PyPI 1.3.0 release cannot recognize some current OpenSSL versions. Docker checks the real certificate verifier during its build. Gunicorn loads the ASK model and crypto stack before accepting requests to avoid a cold first voice response.

The container runs without root and with a read-only filesystem. Compose publishes only `127.0.0.1:8091`. `/health` reports process availability and whether a skill ID is configured; it does not prove Alexa is enabled or IGW is reachable. Unsigned `POST /alexa` must return `400` after installation.

If the existing zone's Bot Fight Mode challenges Amazon, the optional [Workers VPC relay](deploy/worker-vpc/README.md) provides a separate `workers.dev` endpoint through a fixed private service binding. It preserves signed bytes and the NAS verifier, and does not change the zone's security rules. Workers VPC is beta; local relay tests do not prove live transport. The dated validation snapshot records deployment and Amazon test status.

For non-container development:

```bash
.venv/bin/python -m pip install '.[webhook]'
.venv/bin/python -m pip install --requirement requirements-webhook.lock
.venv/bin/gunicorn --config python:amazon_echo_home_voice.gunicorn_config --bind 127.0.0.1:8091 --workers 2 --threads 4 --timeout 10 amazon_echo_home_voice.webhook:application
```

## Optional Lambda deployment

Use only an AWS account authorized for this home project. The deployment script requires an explicitly selected profile. Lambda needs no external Python packages; the Alexa trigger authenticates invocation, and handler code independently verifies ID and timestamp.

1. Create the custom skill and import/build the model as above.
2. In your chosen AWS account/region, create an existing Secrets Manager secret with a `read_token` JSON property. For outbound Access, create another with `client_id` and `client_secret`. Supply ARNs, never raw secret values, to CloudFormation. The deployment identity needs permission to resolve those secrets.
3. Install AWS CLI and SAM CLI. Set `AWS_PROFILE`, `AWS_REGION` (for example `us-east-1`), `ASK_SKILL_ID`, `IGW_URL`, `IGW_READ_TOKEN_SECRET_ARN`, optionally `CF_ACCESS_SECRET_ARN` and `STACK_NAME`.
4. Run `bash scripts/deploy-lambda.sh`. It builds and deploys `template.yaml`. Secrets resolve into Lambda's encrypted environment; people permitted to read Lambda configuration can access them. Rotate and redeploy when needed; rotating a secret alone does not refresh the environment.
5. Set the skill endpoint to the stack's `LambdaArn` output. SAM restricts the Alexa event trigger to `AskSkillId`. Do not add an unrestricted trigger or public Lambda Function URL.
6. Enable Development testing and complete the checklist.

References: [custom skill Lambda hosting](https://developer.amazon.com/en-US/docs/alexa/custom-skills/host-a-custom-skill-as-an-aws-lambda-function.html), [SAM AlexaSkill event](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/sam-property-function-alexaskill.html), [request/response format](https://developer.amazon.com/en-US/docs/alexa/custom-skills/request-and-response-json-reference.html).

## Failure behavior

Each IGW request has a three-second socket timeout, no retry/redirect, a 32 KiB response cap, and certificate-validated HTTPS on port 443. Authentication/network errors, login HTML, bad JSON, unknown schema/status, invalid speech, disconnected-but-fresh reports, and envelopes older than 30 seconds produce: **Home energy data is unavailable right now. Please try again later.** No credentials or remote error bodies are spoken or logged.

`IGW_TIMEOUT_SECONDS` can be 0.1–4; `IGW_MAX_AGE_SECONDS` can be 1–60. Five seconds of future clock skew is tolerated. There is no local response cache. Keep clocks synchronized. Envelope age does not prove sensor age: IGW must identify stale sensor data in its report status/text. The adapter preserves those explanations rather than substituting zero or stale fresh speech.

## Tests and physical Echo checklist

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Tests cover five intents, help/stop/lifecycle, application IDs, timestamps, unsupported requests, GET authorization, redirects, size/contract validation, stale data, network errors, and webhook signature-verifier gating. With the optional webhook extra installed, tests additionally verify real SHA-256 signatures, tamper rejection, timestamp rejection, and Amazon certificate URL restrictions. CI builds the container and imports the real certificate verifier.

- Check CLI values against IGW and Cerbo GX.
- Enable Development testing and use the Echo/Alexa app signed into the same developer account, in English (US).
- Say **Alexa, open home energy skill**, then ask a follow-up question. This explicit phrase was verified in the Alexa+ simulator; the invocation name remains **home energy**.
- Say **Alexa, ask home energy what is the battery charge**.
- Say **Alexa, ask home energy what is the solar power**.
- Say **Alexa, ask home energy how much solar energy did we produce today**.
- Say **Alexa, ask home energy are there any alarms**.
- Say **Alexa, ask home energy what is the energy status**.
- Verify help, stop, and an unsupported request.
- In a test environment, exercise stale/disconnected/unconfigured readings and gateway failure. Do not interrupt live control equipment to test speech.

Simulator and automated test success do not prove physical microphone recognition or playback. Public distribution would additionally require per-household isolation and account linking before sharing the backend.
