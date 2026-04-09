"""Re-score old entries missing structured intel fields (tools_detected, company_profile, etc.)
through the current scorer prompt, then regenerate intel.json."""

import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scorer import score_thread
from profiler import aggregate_profiles
from report import DATA_FILE, DATA_JS_FILE, INTEL_FILE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def needs_rescore(entry: dict) -> bool:
    """Return True if this entry was scored with the old schema."""
    sr = entry.get("scorer_result", {})
    return "company_profile" not in sr or "tools_detected" not in sr


def main():
    data = json.loads(DATA_FILE.read_text())
    to_rescore = [d for d in data if needs_rescore(d)]
    already_ok = len(data) - len(to_rescore)

    logger.info("Total entries: %d | Already have full schema: %d | Need re-scoring: %d",
                len(data), already_ok, len(to_rescore))

    if not to_rescore:
        logger.info("Nothing to re-score.")
        return

    # Build lookup for in-place update
    by_id = {d["thread"]["id"]: d for d in data}

    success = 0
    failed = 0
    for i, entry in enumerate(to_rescore, 1):
        tid = entry["thread"]["id"]
        thread = entry["thread"]
        logger.info("[%d/%d] Re-scoring: %s — %s", i, len(to_rescore), tid, thread["title"][:60])

        result = score_thread(thread)
        if result is None:
            logger.warning("  Failed — keeping old scorer_result")
            failed += 1
            continue

        # Merge: keep the old score/pain_points if new ones are worse, but add structured fields
        by_id[tid]["scorer_result"] = result
        success += 1

        # Rate limit: ~10 req/s is safe for Gemini Flash
        if i % 10 == 0:
            time.sleep(1)

    logger.info("Re-scoring complete: %d success, %d failed", success, failed)

    # Write updated data.json
    all_entries = sorted(by_id.values(), key=lambda e: e.get("added_at", ""), reverse=True)
    DATA_FILE.write_text(json.dumps(all_entries, indent=2, default=str))
    logger.info("Updated data.json with %d entries", len(all_entries))

    # Regenerate intel
    intel = aggregate_profiles(all_entries)
    INTEL_FILE.write_text(json.dumps(intel, indent=2, default=str))
    logger.info("Intel: %d threads, %d tools, %d pain categories, %d industries",
                intel["total_threads_analyzed"], len(intel["tools"]),
                len(intel["pain_point_categories"]), len(intel["industries"]))

    # Update data.js
    data_js = "var DATA = %s;\nvar INTEL = %s;\n" % (
        json.dumps(all_entries, indent=2, default=str),
        json.dumps(intel, indent=2, default=str),
    )
    DATA_JS_FILE.write_text(data_js)
    logger.info("Updated data.js")

    print(f"\nDone. Re-scored {success}/{len(to_rescore)} entries ({failed} failed).")
    print(f"Intel now covers {intel['total_threads_analyzed']} threads:")
    print(f"  Tools: {len(intel['tools'])}")
    print(f"  Pain categories: {len(intel['pain_point_categories'])}")
    print(f"  Industries: {len(intel['industries'])}")
    print(f"  Named companies: {len(intel['named_companies'])}")


if __name__ == "__main__":
    main()
