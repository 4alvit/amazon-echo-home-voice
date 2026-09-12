output "alexa_endpoint" {
  description = "Candidate HTTPS endpoint: check connector/TLS/rejections, then configure Development testing and verify signed requests."
  value       = "https://${var.alexa_hostname}/alexa"
}

output "dedicated_tunnel_id" {
  description = "New tunnel ID for the operator's separate protected connector-token retrieval. No token is fetched or output by this stack."
  value       = cloudflare_zero_trust_tunnel_cloudflared.alexa.id
}

output "bot_management_owned_here" {
  description = "False means this stack leaves the existing zone bot settings under their current owner."
  value       = var.manage_bot_fight_mode
}
