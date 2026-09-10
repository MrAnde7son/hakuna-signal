# Hakuna Signal

Agentic intelligence tool that scans practitioner discussions across Reddit, Spiceworks, Tenable Community, PeerSpot, G2, and more for sales opportunities and market signal across vulnerability & exposure management, endpoint management, patch management, and configuration management / security hardening — and drafts founder-voice replies for the Reddit ones.

Competitors tracked include Tenable, Qualys, Rapid7, and the exposure-management field alongside NinjaOne, Automox, Action1, Ivanti, Tanium, Furl, and Remedio (formerly GYTPOL) on the endpoint/patch/config side.

## How It Works

1. **Fetch** — Pulls recent items from each configured source/category (subreddits, Discourse boards, Khoros forums, review feeds)
2. **Dedup** — Skips items already processed (composite `(source, id)` key in local SQLite)
3. **Keyword filter** — Cheap pre-LLM pass that discards noise (job posts, CTFs, homework) and waves through high-signal topics (competitor mentions, pain keywords). Vendor-curated and review categories bypass the filter entirely.
4. **Score** — Sends surviving items to Gemini 2.5 Flash (Vertex AI) for relevance scoring (1–10) across VM/EM + endpoint/patch/configuration management, plus structured intel: pain points, tools mentioned, company profile, team functions
5. **Draft** — Reddit threads scoring 7+ get a founder-voice comment drafted by the LLM. Other sources feed the Intel tab only — replying on a forum/review site is a different workflow.
6. **Dashboard** — Appends results to a single HTML dashboard (Opportunities + Market Intelligence tabs) with filters and drill-down

## Monitored Sources

| Source | Categories |
|--------|------------|
| Reddit | `sysadmin` · `nessus` · `tenable` · `cybersecurity` · `AskNetsec` · `netsec` · `qualys` · `crowdstrike` · `ciso` · `patchmanagement` · `SCCM` · `Intune` · `msp` · `ITManagers` |
| Spiceworks | `security` · `vendors` · `software-applications` |
| Tenable Community | `vulnerability-watch` · `tenable-research-release-highlights` · `product-announcements` |
| PeerSpot | `vulnerability-management` · `patch-management` · `unified-endpoint-management-uem` · `configuration-management` |
| G2 | `vulnerability-management` · `exposure-and-asset-management` · `appsec-and-cloud` · `endpoint-and-patch-management` |
| Hacker News | `vulnerability-scanner` · `vulnerability-management` · `attack-surface` · `exposure-management` · `patch-management` · `asset-discovery` · `endpoint-management` · `security-hardening` |
| GitHub | scanner / asset / fleet / hardening OSS repos (nuclei, osquery, fleet, wazuh, openscap, lynis, …) |
| Gartner Peer Insights | `vulnerability-assessment` · `exposure-management` · `attack-surface-management` · `endpoint-management` |
| Rapid7 Discuss · ServiceNow · Stack Exchange · arXiv | see `src/config.py` |

## Setup

### Prerequisites

- Python 3.11+
- GCP credentials configured (`gcloud auth application-default login`)
- Access to Vertex AI with `gemini-2.5-flash` enabled

### Install

```bash
git clone <repo-url> && cd hakuna-signal
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure

```bash
cp .env.example .env
```

Edit `.env` with your values:

| Variable | Default | Description |
|----------|---------|-------------|
| `HAKUNA_SIGNAL_USER_AGENT` | `hakuna-signal/1.0` | User-Agent sent on all outbound HTTP requests |
| `GCP_PROJECT` | `hakuna-prod-2026` | GCP project ID |
| `GCP_REGION` | `us-east5` | Vertex AI region |
| `RUN_INTERVAL_MINUTES` | `30` | Minutes between scans |
| `MIN_RELEVANCE_SCORE` | `7` | Minimum score (1–10) to draft a comment |

## Usage

### Run continuously (scheduled)

```bash
python src/main.py
```

Runs the pipeline immediately, then repeats every `RUN_INTERVAL_MINUTES`. Each run appends new opportunities to `reports/data.json` and writes the dashboard to `reports/dashboard.html`.

The dashboard supports client-side filtering by source/category, score, date range, tools, pain categories, team function, industry, company size, security team size, and maturity. Threads are marked **NEW** (found this run) or **SEEN** (carried forward from a previous run).

### Run once

Ctrl+C after the first run completes, or modify `src/main.py` to call `run_pipeline()` directly.

## Project Structure

```
src/                  — Application code
  main.py             — Pipeline orchestrator and scheduler
  config.py           — Config loading from .env, source registry
  sources/            — Per-source fetchers (reddit, spiceworks, tenable, peerspot, g2)
  keyword_filter.py   — Pre-LLM keyword filtering rules
  scorer.py           — LLM relevance scoring + structured intel extraction
  drafter.py          — LLM comment drafting in founder voice (Reddit only)
  profiler.py         — Aggregates structured intel for the Market Intelligence tab
  report.py           — Dashboard generator (single-page HTML + JSON data)
  db.py               — SQLite dedup/tracking layer
  cloud_entrypoint.py — Cloud Run Job entrypoint (hydrate state → run → push state)
  dashboard_server.py — Cloud Run service that serves the dashboard from GCS
scripts/              — One-off backfills and migrations (run from project root)
web/                  — Source-of-truth dashboard.html and favicon.svg
data/                 — Local SQLite db (gitignored at runtime)
reports/              — Generated data.json / intel.json / dashboard.html (gitignored)
docs/                 — Design docs
infra/                — Terraform: Cloud Run Job + Scheduler + GCS state + IAP-protected dashboard LB
```

## Cost

~$2.50/month in Vertex AI API calls at default settings.

## Deployment

Designed to run as a Cloud Run Job triggered by Cloud Scheduler, with state mirrored to a GCS bucket. The dashboard is served by a separate Cloud Run service behind an IAP-protected HTTPS load balancer. Can also run locally or on any VPS — SQLite handles dedup with no external database required.

> **Note on infra naming:** the live GCP resources still use the legacy `reddit-inbound-*` prefix from before the multi-source pivot. The Terraform `var.name` default is preserved at `reddit-inbound` to avoid destroy-recreate cycles on existing buckets, service accounts, and the dashboard LB. Migrating to `hakuna-signal-*` resource names is a separate operation involving fresh buckets and a state copy.
