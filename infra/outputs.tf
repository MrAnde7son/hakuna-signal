output "image_uri" {
  description = "Push container images here"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/${var.name}"
}

output "state_bucket" {
  description = "Pipeline state bucket (seen_threads.db + reports/)"
  value       = google_storage_bucket.state.name
}

output "job_name" {
  value = google_cloud_run_v2_job.pipeline.name
}

output "job_service_account" {
  value = google_service_account.job.email
}

output "build_command" {
  description = "Build + push the image (run from repo root)"
  value       = "gcloud builds submit --tag ${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/${var.name}:latest"
}

output "manual_run_command" {
  description = "Trigger a one-off run for testing"
  value       = "gcloud run jobs execute ${google_cloud_run_v2_job.pipeline.name} --region=${var.region}"
}

output "logs_command" {
  description = "Tail logs from the most recent execution"
  value       = "gcloud beta run jobs logs read ${google_cloud_run_v2_job.pipeline.name} --region=${var.region} --limit=200"
}

# ---------- Dashboard ----------

output "dashboard_ip" {
  description = "Anycast IP for the dashboard LB. Point your DNS A record at this."
  value       = google_compute_global_address.dashboard.address
}

output "dashboard_url" {
  value = "https://${var.dashboard_domain}"
}

output "dashboard_dns_setup" {
  description = "DNS record (managed by Terraform if cloudflare_zone is set, else create manually)"
  value       = "A  ${var.dashboard_domain}  ->  ${google_compute_global_address.dashboard.address}"
}

output "dashboard_cert_status_command" {
  description = "Watch managed cert provisioning (goes ACTIVE 15-60 min after DNS resolves)"
  value       = "gcloud compute ssl-certificates describe ${google_compute_managed_ssl_certificate.dashboard.name} --global --format='value(managed.status,managed.domainStatus)'"
}
