# Home Energy for Amazon Echo

A read-only **Alexa custom skill** for battery charge, current solar power, solar energy today, alarms, and system status. It reads centrally formatted English reports from `inverter-gateway` (IGW). Home Assistant is not required.

```text
Cerbo GX / Venus OS → IGW /v1/energy → this adapter → Alexa → Echo
```

IGW owns device selection, units, aggregation, freshness, and wording. For each report, the adapter makes one authenticated IGW GET and copies `reports.<name>.text` into an Alexa `PlainText` response. It has no MQTT credentials, calculations, or write commands. This repository replaces earlier Home Assistant YAML examples.

For one home, start with [creating the skill and installing its backend](#self-hosted-webhook-no-aws-account-needed), then [enable it on your Echo](#enable-the-skill-on-your-echo). For a shared skill serving separate homes, use [account linking and household setup](#multiple-households-and-account-linking). Both modes use the same [voice commands](#everyday-voice-commands). This repository does not have a published Home Energy store listing; development, certification, and store distribution remain separate steps.

## Included and still required

The repository includes an English (US) interaction model with invocation name **home energy**, a Python Lambda handler, a signature-verified self-hosted HTTPS webhook, a dependency-free smoke CLI, automated security and isolation tests, Docker Compose, and an optional personal-mode AWS SAM template. Multi-household mode adds OAuth account linking, a household connection portal, encrypted persistent storage, and a self-hosted Keycloak deployment example.

See the [anonymized verification summary](docs/validation-2026-09-12.md) for completed checks and the remaining physical Echo test. Deployment examples contain placeholders, not an operator's account identifiers, network topology, credentials, or live energy readings.

For a private installation on the home k3s cluster, see [deployment instructions](deploy/README.md) and [the ClusterIP workload](deploy/k3s.yaml). That path keeps the skill disabled until a real skill ID is configured.

You can install the backend while developer registration is unfinished by leaving `ASK_SKILL_ID` empty. Health then reports `skill_configured: false`, and every Alexa request is rejected until a real skill ID is supplied.

**Installing the backend does not create or enable an Alexa skill.** The Developer Console must contain a custom skill with this model and the installed endpoint; Development testing must be enabled. A physical Echo test is a separate step. No cloud accounts or credentials are included. The code supports personal and multi-household deployments. It has not been publicly certified by Amazon.

The default `ENERGY_VOICE_MODE=personal` uses one operator-managed gateway. Keep that mode in **Development**, limited to your own Amazon account and explicitly trusted test accounts. The application ID authenticates the skill, not a household or speaker. Public distribution requires `ENERGY_VOICE_MODE=multi_household`, the account-linking setup below, and completion of the certification and release checks.

## Multiple households and account linking

Set up the [multi-household stack and account linking](docs/account-linking.md) before inviting separate households. The complete [deployment example](deploy/multi-household/README.md) includes a production-mode Keycloak identity provider, PostgreSQL, the Alexa webhook, and a separate connection portal. It runs on your own server; it does not require a paid identity service or a Cloudflare upgrade. Cerbo GX continues to supply telemetry only.

```text
Homeowner → connection portal → Keycloak sign-in → encrypted household connection
Alexa app → Keycloak account linking → scoped account token
Signed Alexa request → validate account token → that household's IGW → speech
```

Each identity-provider account owns one home's IGW connection. Different homes use different accounts and read tokens. The portal and Alexa clients share the same issuer and stable subject identifiers. This version does not select between multiple homes in one account or use individual Alexa voice profiles; anyone able to use the linked Amazon account's Echo may hear that home's reports.

A homeowner signs in to the portal, enters their public HTTPS `/v1/energy` URL, a dedicated IGW read token, and optional Cloudflare Access service credentials. **Verify and save connection** checks the gateway once. They then enable the skill in Alexa and link the same Home Energy account. A missing or invalid account token produces Alexa's **LinkAccount** card; a linked account without a configured home receives setup instructions.

Every energy request validates its account token with the identity provider. Only that account's encrypted connection is selected; there is no fallback to `IGW_URL` or the operator's home in multi-household mode. Tokens from the portal client cannot authorize Alexa requests. IGW credentials are never sent to Amazon. Public gateway requests reject private or special IPs, validate every DNS answer, pin the network destination while retaining hostname TLS verification, and reject redirects. Private LAN-only gateway addresses remain supported only in personal mode.

The portal can replace a connection or **Disconnect this home**, which deletes its saved credentials and prevents new reports. A request already in progress may finish. Disable the skill in Alexa and revoke the provider session when unlinking account access; portal sign-out alone does not revoke Alexa. Storage and its encryption key need separate private backups. See the guide for recovery, revocation, account deletion, and release verification.

The existing Worker relay remains restricted to `POST /alexa`. The portal and identity provider need separately configured HTTPS routes; the old relay cannot carry browser sign-in or OAuth token traffic. The SQLite-backed multi-household implementation is for a **single persistent server**, with several local Gunicorn workers allowed. Do not run it on independent replicas, a shared network filesystem, or the stateless Lambda example.

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

Download or clone this repository and run the commands from its root. Complete the local `.env` setup above. You need an Amazon developer account and an Echo registered to that same Amazon account; the self-hosted option does not require an AWS account.

1. In the [Alexa Developer Console](https://developer.amazon.com/alexa/console/ask), choose **Create Skill**. Use a display name such as **Home Energy**, **English (US)**, the **Custom** interaction model, and **Provision your own backend resources**. Start from scratch when prompted for a template. Copy this new skill's ID to `ASK_SKILL_ID` in your private `.env`.
2. Open **Build → Interaction Model → JSON Editor**, replace the model with `skill-package/interactionModels/custom/en-US.json`, then save and build. Wait for a successful build. Verify that **Invocation** is **home energy**; the display name and spoken invocation are separate settings.
3. Configure `.env`, then install the backend:

   ```bash
   docker compose up --build -d
   curl --fail http://127.0.0.1:8091/health
   ```

4. Choose [Plan A or Plan B](#cloudflare-deployment-plan-a-and-plan-b) below, or publish the exact `/alexa` path through your own trusted TLS reverse proxy. Other paths must return `404`. The provided Terraform examples require a native Tunnel connector on the same host as the backend, reaching `http://127.0.0.1:8091`. A connector on another host or inside an isolated container cannot reach that loopback port. Keep the backend's host port on loopback.
5. Under **Build → Endpoint**, select **HTTPS**, put the complete URL ending in `/alexa` in **Default Region**, choose the option matching its trusted certificate, and save. A certificate whose wildcard covers this hostname uses the wildcard/subdomain option. **Inbound Alexa must not face a browser login, Access challenge, or service-token requirement.** Alexa does not send your Cloudflare credentials. Protect the outbound IGW endpoint separately.
6. Open **Test**, select **Development** for skill testing and **English (US)** for the simulator. Enter `ask home energy for battery status` and confirm a spoken report. Test all five reports, then follow the Echo activation steps below. A successful model build alone does not test the backend.

These instructions install **personal mode**, without account linking. Gateway credentials remain in the backend configuration; do not enter them into the Alexa app. Leave this installation in Development. For a shared deployment, follow the separate [multi-household instructions](docs/account-linking.md). Refer to Amazon's [skill creation guide](https://developer.amazon.com/en-US/docs/alexa/devconsole/create-a-skill-and-choose-the-interaction-model.html) and [Console testing guide](https://developer.amazon.com/en-US/docs/alexa/devconsole/test-your-skill.html) if Console labels change.

Inbound requests require Amazon's official ASK certificate-chain verification, SHA-256 signature over the original raw body, a 150-second timestamp tolerance, and the exact application ID in context and session. Missing configuration, wrong signatures/IDs, old requests, oversized bodies, or unknown request types fail closed. There is no verification-disable switch. See [Amazon HTTPS requirements](https://developer.amazon.com/en-US/docs/alexa/custom-skills/host-a-custom-skill-as-a-web-service.html) and [official Python verifier](https://github.com/alexa/alexa-skills-kit-sdk-for-python/tree/master/ask-sdk-webservice-support).

The webhook extra pins the upstream [oscrypto OpenSSL parsing fix](https://github.com/wbond/oscrypto/commit/d5f3437ed24257895ae1edd9e503cfb352e635a8) because its PyPI 1.3.0 release cannot recognize some current OpenSSL versions. Docker checks the real certificate verifier during its build. Gunicorn loads the ASK model and crypto stack before accepting requests to avoid a cold first voice response.

The container runs without root and with a read-only filesystem. Compose publishes only `127.0.0.1:8091`. `/health` reports process availability and whether a skill ID is configured; it does not prove Alexa is enabled or IGW is reachable. Unsigned `POST /alexa` must return `400` after installation.

For non-container development:

```bash
.venv/bin/python -m pip install '.[webhook]'
.venv/bin/python -m pip install --requirement requirements-webhook.lock
.venv/bin/gunicorn --config python:amazon_echo_home_voice.gunicorn_config --bind 127.0.0.1:8091 --workers 2 --threads 4 --timeout 10 amazon_echo_home_voice.webhook:application
```

## Enable the skill on your Echo

After the backend and simulator work, enable your development skill in the Alexa mobile app:

1. Sign in with the **same Amazon account** used in the Developer Console. The Echo must also be registered to that account. Set the Echo's language to **English (United States)** to match the shipped `en-US` model.
2. Open the app's menu and **Skills & Games**. In the Alexa+ interface, the route is **More → Alexa+ Store → Browse Alexa Skills and Games**.
3. Open **Your Skills → Dev**, select your skill's display name, and choose **Enable to Use** if it is not already enabled. A development skill is found in this private list, not by searching public store listings.
4. Say **Alexa, ask home energy for battery status** to the Echo. Then try the other reports below.

When testing Alexa on the phone itself, its Alexa language must also match `en-US`. Menu labels can vary by app version; Amazon documents the current [app activation and device testing steps](https://developer.amazon.com/en-US/docs/alexa/test/test-your-skill-overview.html#test-your-skill-with-the-alexa-app). Other Amazon accounts require a separately configured trusted test arrangement; installing this repository does not make the skill available to every household.

## Everyday voice commands

Use a complete request to start from outside the skill:

- **Alexa, ask home energy for battery status.**
- **Alexa, ask home energy for solar power.**
- **Alexa, ask home energy for solar energy today.**
- **Alexa, ask home energy for alarm status.**
- **Alexa, ask home energy for system status.**

To start a conversation, say **Alexa, open home energy skill**. After its welcome prompt, say **battery status**, **solar power**, **solar energy today**, **alarm status**, or **system status** without repeating the invocation. Say **help** for the available requests and **stop** or **cancel** to exit. Each energy report ends the session; use a complete request for the next report. Reports use the gateway's current English wording, including unavailable or stale-data explanations. See the [utterance catalog](docs/utterance-catalog.md) for additional supported phrases.

## Installation troubleshooting

- **Skill missing from Your Skills → Dev:** check the Amazon account, successful model build, Development testing, and matching locale. No public listing or certification is needed for this private setup.
- **Alexa does not find Home Energy:** check the spoken invocation is `home energy`, then try the explicit `open home energy skill` phrase. Confirm the skill is enabled on the intended account and the device language is English (US).
- **Skill response is HTTP 403:** inspect the selected endpoint's Cloudflare security events and authentication policies. A successful probe from your own network does not prove Amazon is allowed through. Follow the documented Plan A or Plan B; keep signature verification enabled.
- **The skill says energy data is unavailable:** run the local `energy-voice status` check and inspect gateway reachability, the read token, report freshness, and optional outbound Access credentials. Keep logs and real configuration private.
- **Simulator works but the Echo does not:** verify app enablement, account registration, device language, microphone, and volume. Simulator success does not establish physical recognition or playback.

## Cloudflare deployment: Plan A and Plan B

Both plans keep Amazon signature, timestamp, and skill-ID verification in the backend. Neither gives Alexa direct access to IGW credentials or control commands. The backend still makes its authenticated read-only request to IGW; Home Assistant is not involved.

### Plan A: Workers VPC relay

```text
Amazon → HTTPS on workers.dev → Worker → fixed VPC Service → Tunnel → backend → IGW
```

Use this plan to leave the existing zone's Bot Fight Mode and other protections unchanged. The Worker exposes only `POST /alexa`, forwards the original signed bytes to one private backend, and has no gateway credentials. The `workers.dev` address is Cloudflare's hosting domain; it does not replace or transfer your own domain. Preview URLs and request logging are disabled in the example.

The [Plan A Terraform example](deploy/terraform/plan-a-worker-vpc/README.md) creates the relay and a narrowly scoped VPC Service using an existing, verified Tunnel and an existing Workers namespace that you supply. It does not adopt shared tunnel ingress or change unrelated Workers. The [relay implementation notes](deploy/worker-vpc/README.md) describe limits, verification, and the alternative Wrangler workflow; choose one tool to own these resources.

**Workers VPC is an open beta.** Cloudflare currently provides VPC without an additional charge during beta; that does not promise permanent free availability. This recipe is intended for **Workers Free only**, with no payment method, paid subscription, or automatic upgrade. The current Free allowance is 100,000 requests per UTC day across the account and 10 ms of CPU per request; waiting for the backend is not CPU time. Quota exhaustion can interrupt service. If VPC becomes paid or unavailable, review Plan B before switching; never silently upgrade. Check [VPC pricing](https://developers.cloudflare.com/workers-vpc/reference/pricing/) and [Workers limits](https://developers.cloudflare.com/workers/platform/limits/) before deployment.

### Plan B: direct Cloudflare Tunnel, without Workers VPC

```text
Amazon → HTTPS on voice.example.com/alexa → dedicated Tunnel → backend → IGW
```

Use this plan when you need a route without the VPC beta dependency and accept the zone-wide Bot Fight Mode tradeoff. The [Plan B Terraform example](deploy/terraform/plan-b-direct-tunnel/README.md) creates a **new, dedicated** remotely managed Tunnel, its complete exact-path ingress, and a proxied DNS record. It does not overwrite an existing shared tunnel's routes. Connect that new tunnel to the backend host using a token kept outside Terraform and Git.

Standard **Bot Fight Mode has no sensitivity setting or hostname/path exception**. WAF Skip rules and Page Rules cannot bypass it. If it challenges Amazon, this plan requires `bot_fight_mode_enabled = false` for the **entire zone**, affecting all its hostnames. The example leaves Bot Management adoption off and Bot Fight Mode enabled by default; disabling requires explicit configuration and review. Existing Bot Management ownership and robots settings must be reconciled first. Other WAF, Access, and geographical rules can still block Amazon and must be checked separately. See [Cloudflare's documented limitations](https://developers.cloudflare.com/bots/get-started/bot-fight-mode/).

Disabling Bot Fight Mode does not disable the backend's signature checks or the separate IGW authentication. It also does not guarantee that the direct route works: validate a real Amazon-signed simulator invocation before switching away from Plan A. Restore Bot Fight Mode explicitly to roll back that setting; removing a Terraform resource from configuration is not a reliable settings rollback.

### Reproduce, verify, and switch

Start with the [Terraform preparation and privacy guide](deploy/terraform/README.md), then follow the chosen example. These are independent root modules, not two configurations to apply blindly together. Supply your own account, zone, namespace, and hostnames in ignored local variable files. Keep API tokens in your environment or secret manager; never commit state, plan files, connector tokens, runtime `.env`, or real identifiers.

Install and configure the backend before publishing an endpoint. Run `terraform init`, `terraform fmt -check`, and `terraform validate`, inspect a saved plan, and apply only the changes you intend. Terraform does not create the Alexa skill, configure IGW, register a developer account, or prove an end-to-end voice response.

Before changing the Alexa Console endpoint, verify trusted TLS, `404` on other paths, and rejection of unsigned `/alexa` requests. Then test all five reports with real Amazon simulator requests, followed by the physical Echo checklist. Keep the previous working route until its replacement succeeds. A direct route still challenged by Bot Fight Mode is a prepared fallback, not an automatic failover. No paid upgrade or shared-zone protection change is part of an automatic switch.

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
- Complete [Echo activation](#enable-the-skill-on-your-echo), then try all five [everyday voice commands](#everyday-voice-commands).
- Verify conversation launch, help, stop, and an unsupported request. The explicit `open home energy skill` phrase was verified in the Alexa+ simulator; the invocation name remains **home energy**.
- In a test environment, exercise stale/disconnected/unconfigured readings and gateway failure. Do not interrupt live control equipment to test speech.

Simulator and automated test success do not prove physical microphone recognition or playback. Public distribution would additionally require per-household isolation and account linking before sharing the backend.
