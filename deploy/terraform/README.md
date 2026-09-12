# Portable Cloudflare deployment examples

These independent Terraform root modules reproduce the networking options in the
[project README](../../README.md#cloudflare-deployment-plan-a-and-plan-b):

- [Plan A: Workers VPC](plan-a-worker-vpc/README.md) uses an existing Tunnel and
  Workers namespace. It preserves the zone's Bot Fight Mode and depends on the
  currently free VPC open beta.
- [Plan B: direct Tunnel](plan-b-direct-tunnel/README.md) creates a new dedicated
  Tunnel and DNS record. It avoids VPC and can explicitly adopt and disable
  standard Bot Fight Mode for the entire zone when Amazon is challenged.

Choose one module and follow its own prerequisites. Neither module installs the
Python backend, configures IGW, creates an Alexa skill, sets a billing plan, or
adds a payment method. Examples use reserved example domains and placeholders;
all installation-specific values belong in your private configuration.

## Prepare the backend and account

Install the signature-verified backend first, configure its real skill ID and
dedicated IGW read token, and verify local health and unsigned-request rejection.
The examples expect a native cloudflared connector on that same host, with the
backend bound to loopback. Do not point a loopback origin at a connector on a
different host or in an isolated container.

Use a Cloudflare Free account with the permissions listed in the selected
module. Supply `CLOUDFLARE_API_TOKEN` through your secret manager or a silent
shell prompt. The provider reads that environment variable directly; do not
write it into Terraform configuration, a variable file, a command argument, or
a public CI secret. The examples have no need for the IGW token, Alexa skill ID,
or backend Cloudflare Access service credentials.

Keep the account on Free. If current terms or a quota require payment, stop and
review a free alternative. These examples neither authorize an upgrade nor
guarantee that a provider will keep a beta feature free indefinitely.

## Keep deployment data private

Terraform state and plans contain real resource identifiers, hostnames, and
configuration even when they contain no application credentials. Keep them in
a private directory or an access-controlled encrypted backend. Git ignore rules
reduce accidental commits; they do not encrypt files or remove files already
tracked by Git.

From the selected module directory, start with:

```bash
umask 077
cp terraform.tfvars.example terraform.tfvars
```

Edit only the local `terraform.tfvars` with your own values. Do not rename it to
the tracked example filename. Never put real domains, account or tunnel IDs,
connector tokens, personal filesystem paths, screenshots, API responses, plan
output, or live energy reports in an issue, commit, or documentation example.
Do not enable Terraform debug logging while using credentials. Retrieve and
store connector tokens separately as described in the module README.

The repository ignores `.terraform/`, local `*.tfvars` and `*.tfvars.json`,
Terraform state, `*.tfplan` saved plans, and crash/override files. Provider lock
files and placeholder-only `*.tfvars.example` files are intended for Git.

## Validate, review, then apply

Each module pins the Cloudflare provider and declares its Terraform version
requirement. Start with local validation:

```bash
terraform init -backend=false
terraform fmt -check
terraform validate
```

Validation does not contact your account or prove live connectivity. The
repository's CI runs these checks without Cloudflare credentials; it never
applies Terraform. Before deployment, configure your private state backend if
you use one and run `terraform init` again. Authenticate privately, complete the
module-specific preflight checks, and create a plan:

```bash
terraform plan -out=review.tfplan
terraform show review.tfplan
```

Review the exact account, resource ownership, public hostname, binding or origin,
and security changes locally. Stop on unexpected replacement or deletion. Apply
only that reviewed plan when ready:

```bash
terraform apply review.tfplan
```

Do not manage the same resource from multiple states, Terraform and Wrangler,
or overlapping automation. In particular, Plan B must not import an existing
shared tunnel: its full ingress belongs exclusively to a new dedicated tunnel.
If zone Bot Management is already managed elsewhere, make any approved setting
change in that existing configuration instead of importing it again.

## End-to-end verification

Keep the Alexa skill in Development. Verify trusted TLS and rejection behavior,
then configure the skill endpoint and run all five report intents through the
actual Amazon simulator. The simulator exercises Amazon's signature and the
private backend connection; a local unsigned probe cannot replace it. Test an
Echo separately. A sample configuration passing validation is not proof of a
successful cloud deployment.

Do not remove a working route until the replacement has passed those checks.
Review the selected module's rollback procedure before making changes. Plan B
does not act as automatic failover, and no fallback automatically disables
shared-zone protection or upgrades the account.
