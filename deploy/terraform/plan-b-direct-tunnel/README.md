# Plan B: dedicated direct Cloudflare Tunnel

This standalone template describes a direct HTTPS route from Cloudflare to the
NAS Alexa signature verifier. It is an alternative to the Workers VPC relay.
It has no backend configuration, account identifiers, credentials, or live
deployment state. Nothing in this folder deploys automatically.

The template creates a **new dedicated remotely managed tunnel**, its complete
ingress, and a new proxied DNS record. Only the chosen hostname's exact `/alexa`
path reaches `127.0.0.1` on the configured NAS webhook port; all other paths and
hostnames return `404`. The NAS still verifies the Amazon certificate, SHA-256
signature, timestamp, and configured skill ID. Method restrictions stay in the
webhook. `disable_chunked_encoding` preserves the WSGI transport requirement.

## Ownership and prerequisites

- Choose an unused hostname in your existing Cloudflare zone. Confirm no DNS
  record or conflicting hostname route exists. Do not import an existing DNS
  record or shared tunnel into these new resources.
- Run the existing signature-verifying webhook on NAS loopback first. Supply
  its real skill ID and scoped IGW credentials through its protected deployment
  configuration, outside this Terraform folder.
- Install one native `cloudflared` connector, version 2025.4.0 or later, on that
  NAS; this minimum supports `--token-file`. Its network namespace
  must match the loopback webhook. Inspect all tunnel clients: require exactly
  one connector record belonging to this NAS; several edge connections for
  that same record are normal. Do not reuse the connector token on another host.
- The new hostname must not match an Access application requiring login or a
  service token. Amazon cannot provide those credentials. Review zone WAF,
  geographical rules, and bot protections for legitimate Alexa requests. This
  template does not weaken Access, WAF, DDoS protection, or unrelated routes.
- Use Terraform 1.7 or later; conditional import blocks require that version.
  The Cloudflare provider is pinned to 5.24.0. Standard Bot Fight Mode and this
  direct tunnel use Free features; no subscription or payment resource exists.

An existing shared tunnel must stay with its existing configuration owner. If
you must reuse it, use the separately documented reviewed routing procedure in
[tunnel-routing.md](../../tunnel-routing.md), preserving the full ingress and
rechecking drift. Do not substitute its ID into this template or import it here.

## Prepare inputs without applying

Copy `terraform.tfvars.example` to a private, ignored `terraform.tfvars` and
replace the placeholders. Keep the real file, any future state, and plan files
out of Git. Obtain `CLOUDFLARE_API_TOKEN` from your existing secret manager; do
not put credentials in HCL, command arguments, or examples. Clear conflicting
global-key environment variables if using token authentication.

The token needs read/write access for this account's tunnels and this zone's
DNS records. Add Bot Management read/write permission only if this state will
explicitly adopt that existing setting. For offline schema validation, no live
Cloudflare token is needed:

```sh
terraform init -backend=false -input=false
terraform fmt -check
terraform validate
```

Initialization may download the pinned provider. It does not provision Cloudflare
resources. A future operator must choose and secure a state backend before a
deployment; `-backend=false` does not configure remote state. No remote plan,
import, apply, workspace-variable change, or billing operation is part of this
template's validation.

## Optional ownership of standard Bot Fight Mode

By default `manage_bot_fight_mode=false`: this stack does not adopt or change any
zone bot settings. `bot_fight_mode_enabled=true` is also the safe default. If
standard Bot Fight Mode remains enabled externally, it can still challenge
Alexa on this direct hostname; the template does not silently bypass it.

If another Terraform state owns `cloudflare_bot_management` for the zone, leave
management disabled here and add the switch in that owning state. A zone's Bot
Management configuration must have one owner.

To adopt previously unmanaged settings in a future reviewed deployment:

1. Read `/zones/{zone_id}/bot_management` through an authorized read-only API
   client. Save the current response privately and review the whole zone's
   existing bot configuration.
2. Set `existing_is_robots_txt_managed` to its exact current boolean. Provider
   5.24.0 defaults this separate field to `false` if omitted; the template blocks
   adoption while the snapshot input is `null`. Never copy a guessed value from
   another account.
3. Set `manage_bot_fight_mode=true`. The conditional import block then adopts
   the existing zone by the supplied `zone_id`; no manual import is required.
   First review a plan with Bot Fight Mode matching the existing setting and
   require adoption to make no bot-setting changes. Do not apply a duplicate
   owner or unrelated changes.

Standard Bot Fight Mode has no adjustable sensitivity or hostname/path exemption
on Free. Turning it off affects **every hostname in the zone**. Preparing that
alternative requires both `bot_fight_mode_enabled=false` and
`acknowledge_zone_wide_bot_fight_mode_disable=true` while management is enabled.
These inputs describe scope; they are not permission to apply automatically.

Only the desired `fight_mode` value and the unchanged robots.txt snapshot are
configured. Other fields remain optional computed settings. Provider 5.24.0 can
show them as `known after apply` and omits unknown values from its update body;
it does not reliably represent `cf_robots_variant` in state. Before and after
any separately authorized apply, compare the read-only API snapshot to ensure
AI bot blocking, JavaScript detection, crawler protection, robots settings, and
all other existing values are preserved. A plan alone cannot prove API results.

Do not paste Cloudflare's broad plan-migration examples into this resource: they
reset additional bot settings. This template configures no Super Bot Fight Mode
or paid Bot Management features. Deleting the bot resource from state is not an
undo operation; this provider does not reset live bot configuration on destroy.
Re-enabling protection requires an explicit reviewed change to `fight_mode`.

## Future deployment and verification

After an operator reviews and authorizes the complete plan, create only the
dedicated resources. Retrieve the connector token separately through Cloudflare's
dashboard or an authorized client; this template deliberately has no token data
source or token output. Store it in a protected file readable only by the native
connector service account and start that connector using `--token-file`. Do not
put the token in process arguments, shell history, Terraform state, or Git.

The endpoint is not ready until the connector is healthy. Confirm the single
NAS connector, valid HTTPS, and expected rejection behavior: unsigned JSON
`POST /alexa` returns `400`; `/health`, `/`, and `/alexa/` return `404`. A probe
from your own network does not prove Amazon will pass the zone's security rules.
Save the previous Alexa endpoint, then set this candidate HTTPS endpoint in the
skill's Development configuration with the certificate option matching its TLS
certificate. Run Amazon-signed Developer Console tests for all five reports,
then test the intended Echo separately. Restore the previous endpoint on failure
and keep the previous route available until these tests pass. Do not alter the
invocation model or disable the NAS verifier.

Rollback the skill endpoint and remove only these dedicated tunnel/DNS resources
after reviewing their current ownership. Restore the zone's prior Bot Fight Mode
boolean through its actual owner if a disable was authorized; destroying this
stack alone does not restore it. Keep the NAS service and IGW Access intact.

References: [Cloudflare tunnel resource](https://registry.terraform.io/providers/cloudflare/cloudflare/5.24.0/docs/resources/zero_trust_tunnel_cloudflared),
[ingress and WSGI settings](https://registry.terraform.io/providers/cloudflare/cloudflare/5.24.0/docs/resources/zero_trust_tunnel_cloudflared_config),
[Bot Fight Mode limitations](https://developers.cloudflare.com/bots/get-started/bot-fight-mode/),
[provider bot-management schema](https://github.com/cloudflare/terraform-provider-cloudflare/blob/v5.24.0/internal/services/bot_management/schema.go),
and [protected connector token files](https://developers.cloudflare.com/tunnel/reference/run-parameters/#token-file).
