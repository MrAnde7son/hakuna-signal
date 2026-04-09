"""One-shot migration: rename thread.subreddit -> thread.category and add thread.source='reddit'.

Run once after upgrading to the multi-source layout. Operates on reports/data.json
in place. The SQLite migration is handled automatically inside db.get_connection().

After running this script, the file can be deleted.
"""

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_FILE = Path(__file__).parent / "reports" / "data.json"


def main():
    if not DATA_FILE.exists():
        logger.info("No data.json — nothing to migrate.")
        return

    data = json.loads(DATA_FILE.read_text())
    logger.info("Loaded %d entries from %s", len(data), DATA_FILE)

    migrated = 0
    skipped = 0
    for entry in data:
        t = entry.get("thread", {})
        if "subreddit" in t and "category" not in t:
            t["category"] = t.pop("subreddit")
            migrated += 1
        elif "category" not in t:
            # No subreddit and no category — leave it untouched, log it
            logger.warning("Entry %s has neither subreddit nor category", t.get("id"))
            skipped += 1
        if "source" not in t:
            t["source"] = "reddit"

    DATA_FILE.write_text(json.dumps(data, indent=2, default=str))
    logger.info("Migrated %d entries, skipped %d, total %d", migrated, skipped, len(data))


if __name__ == "__main__":
    main()
