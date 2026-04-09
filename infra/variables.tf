variable "project_id" {
  type        = string
  description = "GCP project ID (e.g. hakuna-prod-2026)"
}

variable "region" {
  type        = string
  description = "GCP region for Cloud Run, Artifact Registry, GCS, and Vertex AI"
  default     = "us-east5" # matches config.GCP_REGION default
}

variable "scheduler_region" {
  type        = string
  description = "Cloud Scheduler region. Scheduler isn't available in every region (notably not us-east5), so it lives in its own region and targets the job cross-region."
  default     = "us-central1"
}

variable "name" {
  type        = string
  description = <<-EOT
    Resource name prefix for all GCP resources (Cloud Run Job, GCS buckets,
    service accounts, LB components, etc.). This is intentionally pinned to
    "reddit-inbound" — the legacy name from before the multi-source pivot —
    because changing it would force Terraform to destroy and recreate the
    state bucket (losing seen_threads.db), the dashboard LB, the managed
    SSL cert, and all IAM bindings. Migrating to "hakuna-signal-*" resource
    names is a separate operation that requires fresh buckets and a manual
    state copy.
  EOT
  default     = "reddit-inbound"
}

variable "image_tag" {
  type        = string
  description = "Image tag to deploy on first apply. CI overrides this out-of-band."
  default     = "latest"
}

variable "schedule_cron" {
  type        = string
  description = "Cron expression for the pipeline tick"
  default     = "*/30 * * * *"
}

variable "min_relevance_score" {
  type        = number
  description = "MIN_RELEVANCE_SCORE env passed to the job"
  default     = 7
}

variable "user_agent" {
  type        = string
  description = "User-Agent string sent on all outbound HTTP requests (Reddit, Spiceworks, Tenable, PeerSpot, G2)"
  default     = "hakuna-signal/1.0"
}

# ---------- Dashboard exposure (dashboard.tf) ----------

variable "dashboard_domain" {
  type        = string
  description = "Custom domain for the dashboard, e.g. inbound.hakunahq.com"
}

variable "iap_support_email" {
  type        = string
  description = "Email shown on the IAP consent screen. Must be a Workspace user or a group you own."
}

variable "dashboard_members" {
  type        = list(string)
  description = <<-EOT
    IAM members granted IAP access to the dashboard. Examples:
      ["user:itamar@hakunahq.com"]
      ["domain:hakunahq.com"]
      ["group:team@hakunahq.com", "user:investor@example.com"]
  EOT
  default     = []
}

variable "iap_oauth_client_id" {
  type        = string
  description = <<-EOT
    OAuth 2.0 Web Application client ID used by IAP to perform the OAuth
    handshake. Create at: APIs & Services -> Credentials -> Create OAuth
    client ID -> Web application. Authorized redirect URI must be:
      https://iap.googleapis.com/v1/oauth/clientIds/<this-client-id>:handleRedirect
    Set explicitly (rather than using IAP's Google-managed client) so the
    External brand is honored — the Google-managed path silently falls back
    to org-internal-only on Workspace projects.
  EOT
}

variable "iap_oauth_client_secret" {
  type        = string
  description = "Client secret for var.iap_oauth_client_id. Keep out of VCS."
  sensitive   = true
}

# ---------- Cloudflare DNS (cloudflare.tf) ----------

variable "cloudflare_zone" {
  type        = string
  description = <<-EOT
    Cloudflare zone (apex domain) that owns the dashboard subdomain, e.g.
    "hakunahq.com". Set to "" to skip Cloudflare DNS management entirely
    (you'd then create the A record manually).

    Auth: export CLOUDFLARE_API_TOKEN before `terraform apply`. The token
    needs Zone:DNS:Edit on this zone.
  EOT
  default     = ""
}
