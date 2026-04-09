# Hakuna Signal deployment — Cloud Run Job + Cloud Scheduler + GCS state.
#
# Bootstrap (one-time, run from repo root after `terraform apply`):
#
#   gcloud auth configure-docker us-east5-docker.pkg.dev
#   gcloud builds submit \
#     --tag us-east5-docker.pkg.dev/$PROJECT/reddit-inbound-images/reddit-inbound:latest
#   gcloud run jobs execute reddit-inbound --region us-east5   # smoke test
#
# After bootstrap, Cloud Scheduler fires the job on cron. Re-deploys are
# `gcloud builds submit ...` — Terraform ignores image tag drift on purpose
# so CI can bump it without `terraform apply`.
#
# NOTE: GCP resource names still use the legacy "reddit-inbound" prefix from
# before the multi-source pivot (see var.name). Renaming would force destroy
# and recreate of the state bucket, dashboard LB, IAP setup, service accounts,
# and IAM bindings — so the prefix is intentionally preserved.

terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.0"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

# ---------- APIs ----------

locals {
  apis = [
    "run.googleapis.com",
    "cloudscheduler.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "aiplatform.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "iam.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.key
  disable_on_destroy = false
}

# ---------- Artifact Registry ----------

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "${var.name}-images"
  format        = "DOCKER"
  description   = "Container images for ${var.name}"
  depends_on    = [google_project_service.apis]
}

# ---------- State bucket ----------
#
# Holds seen_threads.db + reports/. Single-writer (one job at a time), so a
# plain bucket is enough — no Cloud SQL needed. Versioning gives us a 30-deep
# undo history if a bad run corrupts the DB.

resource "google_storage_bucket" "state" {
  name                        = "${var.project_id}-${var.name}-state"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      num_newer_versions = 30
    }
    action {
      type = "Delete"
    }
  }

  depends_on = [google_project_service.apis]
}

# ---------- Job service account ----------

resource "google_service_account" "job" {
  account_id   = "${var.name}-job"
  display_name = "${var.name} pipeline runner"
}

resource "google_project_iam_member" "job_aiplatform" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.job.email}"
}

resource "google_storage_bucket_iam_member" "job_state_rw" {
  bucket = google_storage_bucket.state.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.job.email}"
}

resource "google_project_iam_member" "job_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.job.email}"
}

# ---------- Cloud Run Job ----------

resource "google_cloud_run_v2_job" "pipeline" {
  name     = var.name
  location = var.region

  # Allow `terraform destroy` to remove this job. We can lock it later by
  # flipping back to true once the deployment is stable.
  deletion_protection = false

  template {
    template {
      service_account = google_service_account.job.email
      timeout         = "1800s" # 30-min hard cap; pipeline normally finishes in a few minutes
      max_retries     = 1

      containers {
        image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/${var.name}:${var.image_tag}"

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }

        env {
          name  = "GCP_PROJECT"
          value = var.project_id
        }
        env {
          name  = "GCP_REGION"
          value = var.region
        }
        env {
          name  = "STATE_BUCKET"
          value = google_storage_bucket.state.name
        }
        env {
          name  = "DASHBOARD_BUCKET"
          value = google_storage_bucket.dashboard.name
        }
        env {
          name  = "MIN_RELEVANCE_SCORE"
          value = tostring(var.min_relevance_score)
        }
        env {
          name  = "HAKUNA_SIGNAL_USER_AGENT"
          value = var.user_agent
        }
      }
    }
  }

  depends_on = [
    google_project_service.apis,
    google_artifact_registry_repository.images,
  ]

  lifecycle {
    # CI bumps the image tag out-of-band; don't fight it on `terraform apply`.
    ignore_changes = [
      template[0].template[0].containers[0].image,
    ]
  }
}

# ---------- Cloud Scheduler ----------

resource "google_service_account" "scheduler" {
  account_id   = "${var.name}-scheduler"
  display_name = "${var.name} scheduler invoker"
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  location = google_cloud_run_v2_job.pipeline.location
  name     = google_cloud_run_v2_job.pipeline.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler.email}"
}

resource "google_cloud_scheduler_job" "tick" {
  name             = "${var.name}-tick"
  region           = var.scheduler_region
  schedule         = var.schedule_cron
  time_zone        = "Etc/UTC"
  attempt_deadline = "320s" # only needs to *start* the job; the job runs async
  description      = "Triggers the ${var.name} Cloud Run Job"

  retry_config {
    retry_count = 1
  }

  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.pipeline.name}:run"

    oauth_token {
      service_account_email = google_service_account.scheduler.email
    }
  }

  depends_on = [google_project_service.apis]
}
