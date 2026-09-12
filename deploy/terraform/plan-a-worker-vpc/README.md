# Plan A: a Worker with a private VPC service

This standalone Terraform configuration creates one dedicated Worker, one Worker
version and deployment, and one HTTP VPC service. It reuses the repository's
[verified relay](../../worker-vpc/README.md). The relay forwards only
`POST /alexa` through its `ALEXA_BACKEND` service binding; other paths return 404.
The Python backend verifies Amazon's request signature, timestamp, and exact skill
ID before retrieving energy reports from IGW.

The Cloudflare account must already have a `workers.dev` namespace and a suitable
tunnel. This configuration does not register a namespace, create or configure a
tunnel, change DNS, alter Bot Fight Mode or Access, or manage any other Worker.
The public URL uses `workers.dev`; an existing domain such as `example.com` keeps
its current configuration.

## Prerequisites and Free-only constraint

- Keep the account on **Workers Free** without adding a payment method,
  subscription, or paid dependency. The configuration contains no billing or
  upgrade resource and cannot prove the account's billing status.
- Workers VPC is an **open beta**, currently free during beta. Standard Workers
  Free limits still apply: 100,000 requests per account per UTC day, shared with
  other Workers, and 10 ms CPU per invocation. Network waiting does not count as
  CPU execution. This is request-based service usage, not a continuously billed
  VM. If Free service becomes unavailable or VPC becomes paid, stop using it or
  choose an explicitly authorized free alternative. Do not upgrade automatically.
- Supply an existing tunnel on the same host/network namespace as the Alexa
  backend. **Every connector of that tunnel must reach the same intended backend
  at `127.0.0.1` on `backend_port`.** A connector on another host makes loopback
  point to that other host. Prefer a dedicated tunnel with a single connector.
- The connector requires `cloudflared >= 2025.7.0` using QUIC. A working public
  tunnel alone does not establish VPC connectivity.
- Install the backend first with `ASK_SKILL_ID` configured and Amazon verification
  enabled. Keep its port on loopback. IGW read credentials and optional Cloudflare
  service credentials belong only in the protected backend environment, never
  in this module, Worker bindings, or Terraform state.
- Use a dedicated unused Worker name. The example never adopts arbitrary
  existing Workers. Configure a Cloudflare API token through
  `CLOUDFLARE_API_TOKEN` in your environment when you intentionally manage cloud
  resources. Scope it to this account, with **Workers Scripts: Edit** (API
  `Workers Scripts Write`, plus read access) and **Connectivity Directory**
  permissions to read, create/update/delete services, and **bind** them to the
  Worker. Cloudflare's example lists **Connectivity Directory: Edit** for service
  management; deployment also requires the **Connectivity Directory Bind** role
  or corresponding token permission. The **Connectivity Directory Admin** role
  covers service creation and binding. A token with only Workers Scripts access
  is insufficient. No DNS, zone security, Access, billing, or tunnel-write
  permission is needed by this configuration. Do not add broad permissions to
  work around a denied operation; verify the required directory permission.
  Do not put credentials in command arguments or tfvars.

See the official [VPC pricing](https://developers.cloudflare.com/workers-vpc/reference/pricing/),
[Workers limits](https://developers.cloudflare.com/workers/platform/limits/), and
[tunnel requirements](https://developers.cloudflare.com/workers-vpc/configuration/tunnel/).
Recheck these terms before deployment; beta availability and pricing can change.
Permission references: Cloudflare's [VPC prerequisites](https://developers.cloudflare.com/workers-vpc/get-started/)
and [official Terraform example](https://github.com/cloudflare/kubernetes-access-worker-example#-cloudflare-api-token-permissions).

## Local validation

Run from this directory. These commands initialize the provider and validate
configuration only; they do not make a Cloudflare plan or apply changes:

```sh
terraform init -backend=false
terraform fmt -check
terraform validate
```

The provider is pinned to `cloudflare/cloudflare = 5.24.0`. Commit the generated
dependency lock file. Keep populated tfvars, state, saved plans, and any backups
private. Local schema validation does not verify account permissions, current
Free eligibility, connector routing, public TLS, or an actual Amazon request.

## Configure and publish in two stages

Copy `terraform.tfvars.example` to `terraform.tfvars` and fill the existing account
ID, existing tunnel UUID, existing namespace label, and a dedicated Worker name.
Leave `publish = false`. The default port is 8091; the host is fixed to
`127.0.0.1`, with no hostname, IPv6, HTTPS, or network-wide fallback.

For a new installation, the expected plan owns exactly four resource addresses:

```text
cloudflare_connectivity_directory_service.alexa
cloudflare_worker.alexa
cloudflare_worker_version.alexa
cloudflare_workers_deployment.alexa
```

When deployment is authorized, review a saved plan before applying it. The first
stage must keep production and previews disabled. Inspect the resulting service
and deployment: exactly one `vpc_service` binding, the expected tunnel and loopback
port, the repository relay hash, and disabled observability, Logpush, previews,
and log consumers. Verify the local backend reports a configured skill and
rejects unsigned requests.

Only then set `publish = true` and review a second plan. This publication step
should change only this Worker's production visibility. If it also changes the
relay, service, tunnel selection, or namespace, stop and complete a disabled
deployment first. Worker creation, version deployment, and publication are
separate provider operations; this configuration does not claim an atomic
publish-after-verification transaction.

The Worker resource checks Cloudflare's reported URL against the requested
existing namespace. Cloudflare provider 5.24.0 does not expose a separate account
namespace data source, so this check can first become available **after the
disabled Worker is created**. Its documented `subdomain.url` is available when
the account has a namespace even if production visibility is false. A missing or
mismatched namespace stops dependent
version/deployment operations; it does not register or change the namespace, and
it does not automatically remove an already-created disabled resource. Inspect
state after an error before retrying. Do not enable production to resolve a
namespace error.

After authorized publication, first check the actual TLS certificate and these
public responses:

- Unsigned `POST /alexa`: 400.
- `POST /alexa` with dummy signature headers and `{}`: the backend rejects it with
  400. Confirm that this request reached the backend to prove the private binding;
  a Worker-only rejection cannot prove connectivity.
- `/`, `/health`, and `/alexa/`: 404.

Save the previous endpoint from the Alexa developer console before changing it.
Set `candidate_endpoint` on the skill in **Development**, choose the certificate
option matching the observed certificate, and test real signed Amazon simulator
requests for the five reports plus launch/help/stop. On failure, restore the
previous verified endpoint and investigate without weakening verification. Keep
the old route until these tests pass; retiring it is a separate decision. Verify
recognition and playback on a physical Echo separately.

Keep the skill in development for its authorized Amazon account. Sharing this
backend through a publicly distributed skill requires per-user authorization and
account linking; a skill ID alone does not identify an individual household.

## Updates, state adoption, and rollback

Do not apply this example into an empty state using the name of an already
deployed Worker. First back up its configuration and import only the dedicated
resources you intentionally own. Provider import formats are:

```text
cloudflare_connectivity_directory_service.alexa: ACCOUNT_ID/SERVICE_ID
cloudflare_worker.alexa: ACCOUNT_ID/WORKER_ID
cloudflare_worker_version.alexa: ACCOUNT_ID/WORKER_ID/VERSION_ID
cloudflare_workers_deployment.alexa: ACCOUNT_ID/WORKER_NAME/DEPLOYMENT_ID
```

These are placeholders, not commands to run unchanged. Review the first plan
after import; differences from a manual deployment can include optional fields,
version metadata, or disabled logging settings. Do not accept replacements or
changes to other projects to obtain a clean plan. Never use a separate
`cloudflare_workers_script_subdomain` resource with `cloudflare_worker`: the two
would manage the same visibility setting.

Normal relay or binding changes create a new immutable Worker version, then move
the deployment to that version at 100 percent. `create_before_destroy` ensures the
new version exists first. Provider 5.24.0 removes replaced versions from Terraform
state without deleting their historical Cloudflare versions. Preserve the prior
version ID if you need an explicit deployment rollback. This is different from
an unexpected replacement of the Worker or private service during state adoption.

To stop public access, review and apply `publish = false` for this Worker. This
leaves the private service and existing tunnel intact. Once the endpoint is no
longer in use, removing only these four dedicated resources is a separate
reviewed teardown. Switching Alexa to Plan B requires a separate explicit
decision and verification of that route; this module never switches endpoints or
changes zone protection automatically.

## Provider references

This configuration follows Cloudflare's [VPC Terraform guide](https://developers.cloudflare.com/workers-vpc/configuration/vpc-services/terraform/)
and the pinned provider schemas for
[VPC services](https://registry.terraform.io/providers/cloudflare/cloudflare/5.24.0/docs/resources/connectivity_directory_service),
[Worker settings](https://registry.terraform.io/providers/cloudflare/cloudflare/5.24.0/docs/resources/worker),
[Worker versions](https://registry.terraform.io/providers/cloudflare/cloudflare/5.24.0/docs/resources/worker_version),
and [deployments](https://registry.terraform.io/providers/cloudflare/cloudflare/5.24.0/docs/resources/workers_deployment).
The provider uses Cloudflare's beta Worker management API for these separate
Worker resources. Neither successful local validation nor the relay's runtime
tests establish that Terraform has been applied to a real account.
