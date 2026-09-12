output "candidate_endpoint" {
  description = "Expected Alexa HTTPS endpoint. This output alone does not establish publication or connectivity."
  value       = "https://${local.worker_hostname}/alexa"
}

output "published" {
  description = "Requested production visibility. Verify the public endpoint after an authorized apply."
  value       = var.publish
}

output "service_id" {
  description = "Private VPC service identifier for inspection or state adoption."
  value       = cloudflare_connectivity_directory_service.alexa.service_id
}

output "relay_sha256" {
  description = "Hash of the existing repository relay included in the Worker version."
  value       = filesha256(local.relay_source)
}
