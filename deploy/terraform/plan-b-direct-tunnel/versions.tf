terraform {
  # Terraform 1.7 introduced for_each in import blocks.
  required_version = ">= 1.7.0, < 2.0.0"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "5.24.0"
    }
  }
}

# Supply CLOUDFLARE_API_TOKEN through the operator's existing secret manager.
provider "cloudflare" {}
