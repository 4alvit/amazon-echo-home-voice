variable "account_id" {
  description = "Cloudflare account containing the zone and the new dedicated tunnel."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9a-fA-F]{32}$", var.account_id))
    error_message = "Supply the operator's 32-character Cloudflare account ID."
  }
}

variable "zone_id" {
  description = "Existing Cloudflare zone; this template does not create or upgrade a zone."
  type        = string
  nullable    = false

  validation {
    condition     = can(regex("^[0-9a-fA-F]{32}$", var.zone_id))
    error_message = "Supply the operator's 32-character Cloudflare zone ID."
  }
}

variable "alexa_hostname" {
  description = "New unused fully qualified DNS hostname in this zone, without a scheme, wildcard, path, or trailing dot."
  type        = string
  nullable    = false

  validation {
    condition = length(var.alexa_hostname) <= 253 && can(regex(
      "^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$",
      var.alexa_hostname
    ))
    error_message = "Use a lowercase DNS hostname such as alexa.example.com; no wildcard, URL, or trailing dot."
  }
}

variable "tunnel_name" {
  description = "Name for a NEW dedicated remotely managed tunnel; do not import an existing shared tunnel here."
  type        = string
  default     = "alexa-direct"
  nullable    = false

  validation {
    condition     = length(trimspace(var.tunnel_name)) > 0 && length(var.tunnel_name) <= 100
    error_message = "Choose a nonempty dedicated tunnel name of at most 100 characters."
  }
}

variable "nas_webhook_port" {
  description = "Loopback port of the NAS signature-verifying Alexa webhook. The connector must share the NAS host network namespace."
  type        = number
  default     = 8091
  nullable    = false

  validation {
    condition     = var.nas_webhook_port >= 1 && var.nas_webhook_port <= 65535 && floor(var.nas_webhook_port) == var.nas_webhook_port
    error_message = "The webhook port must be an integer between 1 and 65535."
  }
}

variable "manage_bot_fight_mode" {
  description = "Opt in to adopting this zone's existing Bot Management settings into this state. Leave false when another Terraform state owns them."
  type        = bool
  default     = false
  nullable    = false
}

variable "bot_fight_mode_enabled" {
  description = "Standard Bot Fight Mode for the ENTIRE zone when management is enabled. False is a whole-zone disable, not a hostname exception or sensitivity setting."
  type        = bool
  default     = true
  nullable    = false
}

variable "acknowledge_zone_wide_bot_fight_mode_disable" {
  description = "Explicitly acknowledge the whole-zone scope before planning an opted-in Bot Fight Mode disable."
  type        = bool
  default     = false
  nullable    = false
}

variable "existing_is_robots_txt_managed" {
  description = "Exact current is_robots_txt_managed value from this operator's read-only zone snapshot. Required when adopting Bot Management; do not guess."
  type        = bool
  default     = null
}
