# This stack creates its own tunnel. Its complete ingress cannot replace a
# shared tunnel's existing services because no existing tunnel ID is accepted.
resource "cloudflare_zero_trust_tunnel_cloudflared" "alexa" {
  account_id = var.account_id
  name       = var.tunnel_name
  config_src = "cloudflare"
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "alexa" {
  account_id = var.account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.alexa.id
  config = {
    ingress = [
      {
        hostname = var.alexa_hostname
        path     = "^/alexa$"
        service  = "http://127.0.0.1:${var.nas_webhook_port}"
        origin_request = {
          # The WSGI verifier requires Content-Length and the exact signed body.
          disable_chunked_encoding = true
        }
      },
      {
        service = "http_status:404"
      }
    ]
  }
}

resource "cloudflare_dns_record" "alexa" {
  zone_id = var.zone_id
  name    = var.alexa_hostname
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.alexa.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
  comment = "Dedicated signature-verifying Alexa webhook"

  depends_on = [cloudflare_zero_trust_tunnel_cloudflared_config.alexa]
}

locals {
  bot_management_zones = var.manage_bot_fight_mode ? { selected = var.zone_id } : {}
}

resource "cloudflare_bot_management" "alexa" {
  for_each = local.bot_management_zones

  zone_id    = each.value
  fight_mode = var.bot_fight_mode_enabled

  # Provider 5.29.0 defaults these separate existing settings to false.
  is_robots_txt_managed     = var.existing_is_robots_txt_managed
  ai_bots_migration_opt_out = var.existing_ai_bots_migration_opt_out

  lifecycle {
    precondition {
      condition     = var.existing_ai_bots_migration_opt_out != null
      error_message = "Read the zone's current bot settings and supply existing_ai_bots_migration_opt_out before adoption."
    }
    precondition {
      condition     = var.existing_is_robots_txt_managed != null
      error_message = "Read the zone's current bot settings and supply existing_is_robots_txt_managed before adoption."
    }
    precondition {
      condition     = var.bot_fight_mode_enabled || var.acknowledge_zone_wide_bot_fight_mode_disable
      error_message = "Disabling standard Bot Fight Mode affects the entire zone. Set the explicit whole-zone acknowledgment only after reviewing that scope."
    }
  }
}

# Conditional adoption avoids silently claiming settings already owned elsewhere.
import {
  for_each = local.bot_management_zones
  to       = cloudflare_bot_management.alexa[each.key]
  id       = each.value
}
