# Cloudflare DNS for the dashboard.
#
# Auth: the provider reads CLOUDFLARE_API_TOKEN from the environment, so the
# token never lands in tfvars or state files. Create one at:
#   https://dash.cloudflare.com/profile/api-tokens
# Required permissions:  Zone -> DNS -> Edit  (scoped to zone "hakunahq.com")
#
# Then before applying:
#   export CLOUDFLARE_API_TOKEN='...'
#   terraform init   # downloads the cloudflare provider on first run
#   terraform apply
#
# To opt out of Cloudflare management, set var.cloudflare_zone = "" and the
# record resource will be skipped — you'd then create the A record by hand.

provider "cloudflare" {
  # api_token sourced from $CLOUDFLARE_API_TOKEN
}

data "cloudflare_zone" "this" {
  count = var.cloudflare_zone == "" ? 0 : 1
  name  = var.cloudflare_zone
}

resource "cloudflare_record" "dashboard" {
  count = var.cloudflare_zone == "" ? 0 : 1

  zone_id = data.cloudflare_zone.this[0].id
  name    = trimsuffix(var.dashboard_domain, ".${var.cloudflare_zone}")
  value   = google_compute_global_address.dashboard.address
  type    = "A"
  ttl     = 1     # 1 = automatic in Cloudflare
  proxied = false # MUST be off — see comment block below

  comment = "hakuna-signal dashboard LB (managed by Terraform)"
}

# Why proxied = false:
#   1. Google's managed SSL cert validates by resolving the domain to the LB
#      IP. With Cloudflare proxy on, validation sees a Cloudflare anycast IP
#      and the cert sits in FAILED_NOT_VISIBLE forever.
#   2. IAP's OAuth redirect + signed-cookie flow is fragile behind any
#      reverse proxy that rewrites headers.
#   3. We don't need Cloudflare's WAF/CDN for an IAP-gated, cached, single-
#      user dashboard.
# Other records on hakunahq.com (marketing site, etc.) can stay proxied —
# the orange/grey cloud is per-record.
