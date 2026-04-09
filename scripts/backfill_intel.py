"""One-time script: backfill opportunity_data into SQLite from data.json, then regenerate intel."""

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import db
from profiler import aggregate_profiles
from report import INTEL_FILE, DATA_JS_FILE, DATA_FILE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    data = json.loads(DATA_FILE.read_text())
    logger.info("Loaded %d entries from data.json", len(data))

    # Backfill SQLite: insert opportunity_data for threads that exist but have NULL data
    conn = db.get_connection()
    backfilled = 0
    inserted = 0
    for entry in data:
        tid = entry["thread"]["id"]
        source = entry["thread"].get("source", "reddit")
        row = conn.execute(
            "SELECT opportunity_data FROM seen_threads WHERE source = ? AND thread_id = ?",
            (source, tid),
        ).fetchone()

        opp_json = json.dumps(entry)

        if row is None:
            # Thread not in DB at all — insert it
            t = entry["thread"]
            conn.execute(
                "INSERT INTO seen_threads (thread_id, source, category, title, score, action, opportunity_data, viewed) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                (tid, source, t.get("category") or t.get("subreddit"),
                 t.get("title", ""),
                 entry["scorer_result"].get("score", 0),
                 "alerted" if entry.get("draft") else "low_score",
                 opp_json),
            )
            inserted += 1
        elif row[0] is None:
            # Thread exists but missing opportunity_data — backfill
            conn.execute(
                "UPDATE seen_threads SET opportunity_data = ? WHERE source = ? AND thread_id = ?",
                (opp_json, source, tid),
            )
            backfilled += 1

    logger.info("Backfilled %d rows, inserted %d new rows into SQLite", backfilled, inserted)

    # Regenerate intel from full data.json
    intel = aggregate_profiles(data)
    logger.info("Aggregated intel: %d threads, %d tools, %d pain categories",
                intel["total_threads_analyzed"], len(intel["tools"]), len(intel["pain_point_categories"]))

    INTEL_FILE.write_text(json.dumps(intel, indent=2, default=str))
    logger.info("Wrote %s", INTEL_FILE)

    # Update data.js so dashboard picks it up
    data_js = "var DATA = %s;\nvar INTEL = %s;\n" % (
        json.dumps(data, indent=2, default=str),
        json.dumps(intel, indent=2, default=str),
    )
    DATA_JS_FILE.write_text(data_js)
    logger.info("Wrote %s", DATA_JS_FILE)

    print(f"\nDone. Intel now covers {intel['total_threads_analyzed']} threads.")
    print(f"  Tools: {len(intel['tools'])}")
    print(f"  Pain categories: {len(intel['pain_point_categories'])}")
    print(f"  Industries: {len(intel['industries'])}")
    print(f"  Named companies: {len(intel['named_companies'])}")


if __name__ == "__main__":
    main()
