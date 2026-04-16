"""Reddit source: fetches recent threads from a subreddit via Reddit's public JSON API.

Reddit aggressively 403s unauthenticated requests from cloud provider egress IPs
(GCP, AWS), so live fetches don't work from Cloud Run. To get around this,
`scripts/fetch_reddit.py` runs on a residential IP, writes each subreddit's
items (with comments pre-hydrated) to `reddit_dump/<category>.json`, and uploads
the dir to `gs://<STATE_BUCKET>/reddit_dump/`. cloud_entrypoint hydrates that
dir on each run, and `fetch_items` here prefers the dump when it exists.

Local dev falls through to live fetch when the dump isn't present.
"""

import json
import logging
from pathlib import Path

import config

from ._http import get_json

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DUMP_DIR = PROJECT_ROOT / "reddit_dump"


def fetch_items(category: str) -> list[dict]:
    """Return recent threads for a subreddit.

    Prefers the pre-crawled dump (residential-IP fetch uploaded to GCS); falls
    back to live HTTP for local dev when no dump is present.

    When loading from dump, `top_comments` is already a list (possibly empty)
    so main.py's deferred-hydrate path is skipped. When live, `top_comments`
    is None and main.py hydrates via `fetch_comments` below — this path will
    403 from cloud IPs but works fine from a residential connection.
    """
    dump_file = DUMP_DIR / f"{category}.json"
    if dump_file.is_file():
        return _load_dump(dump_file)

    return fetch_live(category)


def fetch_live(category: str) -> list[dict]:
    """Live-fetch recent threads for a subreddit via the public JSON API.

    Separate from `fetch_items` so `scripts/fetch_reddit.py` can bypass the
    dump-prefer logic when building a new dump. 403s from cloud egress IPs.
    """
    limit = config.ITEMS_PER_CATEGORY.get("reddit", 100)
    url = f"https://www.reddit.com/r/{category}/new.json?limit={limit}"
    data = get_json(url)
    if not data:
        return []

    items = []
    for post in data.get("data", {}).get("children", []):
        p = post["data"]
        items.append({
            "source": "reddit",
            "id": p["id"],
            "category": category,
            "title": p.get("title", ""),
            "body": p.get("selftext", ""),
            "url": f"https://www.reddit.com{p.get('permalink', '')}",
            "score": p.get("score", 0),
            "created_utc": p.get("created_utc", 0),
            "top_comments": None,
        })

    return items


def _load_dump(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to read reddit dump %s: %s", path, e)
        return []
    return data.get("items", [])


def fetch_comments(subreddit: str, thread_id: str) -> list[str]:
    """Fetch the top 3 comments for a thread."""
    url = f"https://www.reddit.com/r/{subreddit}/comments/{thread_id}.json?limit=3&sort=best"
    data = get_json(url)
    if not data or not isinstance(data, list) or len(data) < 2:
        return []

    comments = []
    for child in data[1].get("data", {}).get("children", []):
        body = child.get("data", {}).get("body", "")
        if body and child.get("kind") == "t1":
            comments.append(body)
        if len(comments) >= 3:
            break

    return comments
