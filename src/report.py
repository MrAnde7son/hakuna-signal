import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = PROJECT_ROOT / "reports"
DATA_FILE = REPORT_DIR / "data.json"
DATA_JS_FILE = REPORT_DIR / "data.js"
INTEL_FILE = REPORT_DIR / "intel.json"
DASHBOARD_FILE = REPORT_DIR / "dashboard.html"
DASHBOARD_TEMPLATE = PROJECT_ROOT / "web" / "dashboard.html"


def generate_report(opportunities: list[dict], intel: dict | None = None):
    """Append opportunities to data.json, regenerate intel from full dataset, and sync dashboard.html."""
    from profiler import aggregate_profiles

    if not opportunities:
        logger.info("No opportunities to report")
        return DASHBOARD_FILE if DASHBOARD_FILE.exists() else None

    REPORT_DIR.mkdir(exist_ok=True)

    # Load existing data
    existing = []
    try:
        existing = json.loads(DATA_FILE.read_text())
    except FileNotFoundError:
        pass
    except json.JSONDecodeError:
        logger.warning("Corrupt data.json — starting fresh")

    # Index existing by thread id for dedup/update
    by_id = {e["thread"]["id"]: e for e in existing}

    now = datetime.now(timezone.utc).isoformat()

    for opp in opportunities:
        tid = opp["thread"]["id"]
        entry = {
            "thread": opp["thread"],
            "scorer_result": opp["scorer_result"],
            "draft": opp["draft"],
            "time_ago": opp["time_ago"],
            "is_new": opp.get("is_new", True),
            "added_at": by_id[tid]["added_at"] if tid in by_id else now,
            "run_at": now,
        }
        by_id[tid] = entry

    all_entries = sorted(by_id.values(), key=lambda e: e["added_at"], reverse=True)
    intel_data = intel if intel is not None else aggregate_profiles(all_entries)

    entries_json = json.dumps(all_entries, indent=2, default=str)
    intel_json = json.dumps(intel_data, indent=2, default=str)

    DATA_FILE.write_text(entries_json)
    logger.info("Data file updated: %d entries in %s", len(all_entries), DATA_FILE)
    INTEL_FILE.write_text(intel_json)
    DATA_JS_FILE.write_text(f"var DATA = {entries_json};\nvar INTEL = {intel_json};\n")

    _sync_dashboard()

    return DASHBOARD_FILE


def _sync_dashboard():
    """Copy the static dashboard template into reports/ if it's missing or stale."""
    src_mtime = DASHBOARD_TEMPLATE.stat().st_mtime
    if DASHBOARD_FILE.exists() and DASHBOARD_FILE.stat().st_mtime >= src_mtime:
        return
    shutil.copyfile(DASHBOARD_TEMPLATE, DASHBOARD_FILE)
    logger.info("Dashboard synced from %s to %s", DASHBOARD_TEMPLATE, DASHBOARD_FILE)
