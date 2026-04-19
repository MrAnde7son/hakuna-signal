"""Fetch Reddit threads from a residential IP and upload to GCS.

Reddit 403s unauthenticated requests from cloud provider egress IPs, so
production never sees Reddit items. Run this script on your laptop (or any
non-cloud host) occasionally; it live-fetches every configured subreddit,
pre-hydrates comments for items that survive the keyword filter, writes
`reddit_dump/<category>.json`, and uploads the dir to
`gs://<STATE_BUCKET>/reddit_dump/`. `cloud_entrypoint.hydrate` downloads that
dir on the next run and `sources.reddit.fetch_items` prefers it over live HTTP.

Usage:
    python scripts/fetch_reddit.py                  # fetch + upload
    python scripts/fetch_reddit.py --no-upload      # local only
    STATE_BUCKET=my-bucket python scripts/fetch_reddit.py
"""
import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import config  # noqa: E402
from keyword_filter import should_process  # noqa: E402
from sources import reddit  # noqa: E402

DUMP_DIR = ROOT / "reddit_dump"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fetch_reddit")


def fetch_subreddit(category: str) -> list[dict]:
    """Live-fetch a subreddit and hydrate comments for items passing the filter.

    Items that fail the filter get `top_comments=[]` (not None) so prod skips
    the deferred-hydrate path — it would 403 trying to reach reddit.com anyway.
    """
    items = reddit.fetch_live(category)
    logger.info("  fetched %d raw items from r/%s", len(items), category)

    hydrated = 0
    for item in items:
        if should_process(item["title"], item["body"], item["score"], "reddit", category):
            # 4s pad on top of _http.py's 3s floor: Reddit's unauth limit is
            # tighter than the 20/min the floor alone implies, and comment
            # fetches batch up fast enough to trip 429s without this.
            time.sleep(4)
            item["top_comments"] = reddit.fetch_comments(category, item["id"])
            hydrated += 1
        else:
            item["top_comments"] = []
    logger.info("  hydrated comments for %d/%d items", hydrated, len(items))

    return items


def write_dump(category: str, items: list[dict]) -> Path:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    path = DUMP_DIR / f"{category}.json"
    payload = {
        "category": category,
        "fetched_at": time.time(),
        "items": items,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def upload(bucket_name: str) -> int:
    from google.cloud import storage

    bucket = storage.Client().bucket(bucket_name)
    count = 0
    for path in sorted(DUMP_DIR.glob("*.json")):
        blob = bucket.blob(f"reddit_dump/{path.name}")
        blob.upload_from_filename(path)
        count += 1
        logger.info("  uploaded gs://%s/reddit_dump/%s", bucket_name, path.name)
    return count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-upload", action="store_true", help="Skip GCS upload")
    parser.add_argument("--subreddit", action="append", help="Only fetch this subreddit (repeatable)")
    args = parser.parse_args()

    subreddits = args.subreddit or config.SOURCES["reddit"]

    for i, category in enumerate(subreddits):
        if i > 0:
            time.sleep(10)  # cool-down between subs
        logger.info("Fetching r/%s", category)
        try:
            items = fetch_subreddit(category)
        except Exception:
            logger.exception("Failed to fetch r/%s — skipping", category)
            continue
        path = write_dump(category, items)
        logger.info("  wrote %s (%d items)", path, len(items))

    if args.no_upload:
        logger.info("Skipping upload (--no-upload)")
        return 0

    bucket_name = os.environ.get("STATE_BUCKET")
    if not bucket_name:
        logger.warning("STATE_BUCKET not set — dump written locally only")
        return 0

    n = upload(bucket_name)
    logger.info("Uploaded %d dump files to gs://%s/reddit_dump/", n, bucket_name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
