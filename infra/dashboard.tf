# Dashboard exposure: inbound.hakunahq.com -> HTTPS LB -> Cloud Run service -> GCS.
#
# After `terraform apply`:
#   1. terraform output dashboard_ip
#   2. At your DNS provider, create an A record:
#        inbound.hakunahq.com -> <dashboard_ip>
#      (apex/wildcard works too — adjust var.dashboard_domain)
#   3. Wait 15-60 min for the managed cert to provision. Check with:
#        gcloud compute ssl-certificates describe reddit-inbound-dashboard-cert \
#          --global --format='value(managed.status)'
#      It should go ACTIVE once DNS is correct.
#   4. Visit https://<dashboard_domain> — IAP will challenge for Google login.
#
# IAP IAM is granted to whatever you put in var.dashboard_members.

# ---------- Dashboard bucket ----------

resource "google_storage_bucket" "dashboard" {
  name                        = "${var.project_id}-${var.name}-dashboard"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "job_dashboard_writer" {
  bucket = google_storage_bucket.dashboard.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.job.email}"
}

resource "google_storage_bucket_iam_member" "dashboard_service_reader" {
  bucket = google_storage_bucket.dashboard.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.dashboard.email}"
}

# ---------- Cloud Run service ----------

resource "google_service_account" "dashboard" {
  account_id   = "${var.name}-dashboard"
  display_name = "${var.name} dashboard service"
}

resource "google_cloud_run_v2_service" "dashboard" {
  name     = "${var.name}-dashboard"
  location = var.region
  # Block direct .run.app access — only the LB (and other GCP LBs) can reach it.
  # Combined with `allUsers` invoker below, this is the canonical "behind a LB
  # with IAP" pattern: ingress filter blocks public traffic, IAP gates the LB.
  ingress = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  deletion_protection = false

  template {
    service_account = google_service_account.dashboard.email

    scaling {
      min_instance_count = 0
      max_instance_count = 2
    }

    containers {
      image   = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/${var.name}:${var.image_tag}"
      command = ["python", "src/dashboard_server.py"]

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
      }

      env {
        name  = "DASHBOARD_BUCKET"
        value = google_storage_bucket.dashboard.name
      }
    }
  }

  depends_on = [
    google_project_service.apis,
    google_artifact_registry_repository.images,
  ]

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
    ]
  }
}

# Safe because the ingress filter above blocks .run.app traffic — `allUsers`
# only takes effect for requests that arrive via a Cloud Load Balancer, which
# in our case is the IAP-protected one below.
resource "google_cloud_run_v2_service_iam_member" "lb_invoker" {
  project  = google_cloud_run_v2_service.dashboard.project
  location = google_cloud_run_v2_service.dashboard.location
  name     = google_cloud_run_v2_service.dashboard.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# IAP needs its own service identity provisioned in the project before it can
# proxy requests through to Cloud Run. New projects don't get this auto-created
# — IAP returns "service account is not provisioned" until you explicitly
# create it. This resource is idempotent: if the identity already exists, TF
# adopts it.
resource "google_project_service_identity" "iap" {
  provider = google-beta
  project  = var.project_id
  service  = "iap.googleapis.com"
}

# Grant the IAP service identity invoker on the dashboard service so it can
# call through after a successful login challenge. The `allUsers` binding
# above isn't sufficient on its own when IAP is doing the proxying — IAP uses
# its own identity, not the caller's.
resource "google_cloud_run_v2_service_iam_member" "iap_invoker" {
  project  = google_cloud_run_v2_service.dashboard.project
  location = google_cloud_run_v2_service.dashboard.location
  name     = google_cloud_run_v2_service.dashboard.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_project_service_identity.iap.email}"
}

# ---------- HTTPS Load Balancer ----------

resource "google_compute_global_address" "dashboard" {
  name = "${var.name}-dashboard-ip"
}

resource "google_compute_region_network_endpoint_group" "dashboard" {
  name                  = "${var.name}-dashboard-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = google_cloud_run_v2_service.dashboard.name
  }
}

resource "google_compute_backend_service" "dashboard" {
  name                  = "${var.name}-dashboard-backend"
  protocol              = "HTTPS"
  load_balancing_scheme = "EXTERNAL_MANAGED"

  backend {
    group = google_compute_region_network_endpoint_group.dashboard.id
  }

  iap {
    enabled              = true
    oauth2_client_id     = var.iap_oauth_client_id
    oauth2_client_secret = var.iap_oauth_client_secret
    # Explicit client (not Google-managed) because the Google-managed path
    # silently uses an org-internal-only OAuth flow on projects under a
    # Workspace org, which 403s any consumer Gmail account regardless of
    # IAP IAM. The explicit client respects the brand's External user_type.
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

resource "google_compute_url_map" "dashboard" {
  name            = "${var.name}-dashboard-urlmap"
  default_service = google_compute_backend_service.dashboard.id
}

resource "google_compute_managed_ssl_certificate" "dashboard" {
  name = "${var.name}-dashboard-cert"

  managed {
    domains = [var.dashboard_domain]
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_target_https_proxy" "dashboard" {
  name             = "${var.name}-dashboard-https-proxy"
  url_map          = google_compute_url_map.dashboard.id
  ssl_certificates = [google_compute_managed_ssl_certificate.dashboard.id]
}

resource "google_compute_global_forwarding_rule" "dashboard_https" {
  name                  = "${var.name}-dashboard-fr-https"
  target                = google_compute_target_https_proxy.dashboard.id
  port_range            = "443"
  ip_address            = google_compute_global_address.dashboard.address
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

# HTTP -> HTTPS redirect so people typing http://inbound.hakunahq.com get bumped up.
resource "google_compute_url_map" "dashboard_http_redirect" {
  name = "${var.name}-dashboard-http-redirect"

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

resource "google_compute_target_http_proxy" "dashboard_http" {
  name    = "${var.name}-dashboard-http-proxy"
  url_map = google_compute_url_map.dashboard_http_redirect.id
}

resource "google_compute_global_forwarding_rule" "dashboard_http" {
  name                  = "${var.name}-dashboard-fr-http"
  target                = google_compute_target_http_proxy.dashboard_http.id
  port_range            = "80"
  ip_address            = google_compute_global_address.dashboard.address
  load_balancing_scheme = "EXTERNAL_MANAGED"
}

# ---------- IAP access control ----------
#
# We don't manage the OAuth brand or client in Terraform — see the comment on
# the iap {} block above. The existing project brand is reused as-is, and
# Google manages the OAuth client behind the scenes when IAP is enabled on a
# backend service. This sidesteps the deprecated IAP OAuth Admin API
# (permanently shut down March 2026) and works with both Internal and
# External brands.
#
# The only thing we manage here is who can pass the IAP login challenge.

resource "google_iap_web_backend_service_iam_member" "dashboard_users" {
  for_each            = toset(var.dashboard_members)
  web_backend_service = google_compute_backend_service.dashboard.name
  role                = "roles/iap.httpsResourceAccessor"
  member              = each.key
}
