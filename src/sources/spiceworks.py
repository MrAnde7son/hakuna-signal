"""Spiceworks Community source: Discourse-based forum, public JSON API.

Verified during planning:
- categories.json lists 13 top-level categories
- /c/{slug}/{id}.json returns a topic listing (30 per page)
- /t/{topic_id}.json returns the full topic with post_stream.posts[]
"""

import logging
from datetime import datetime

import config

from ._http import clean_html, get_json

logger = logging.getLogger(__name__)

BASE = "https://community.spiceworks.com"

# Static map of category slug -> Discourse category id. Resolved by hand from
# categories.json during planning. If we ever need new categories, run:
#   curl -s https://community.spiceworks.com/categories.json | jq '.category_list.categories[] | {slug, id}'
CATEGORY_IDS = {
    "security": 28,
    "vendors": 38,
    "hardware-infrastructure": 15,
    "software-applications": 29,
    "programming-development": 26,
}


def fetch_items(category: str) -> list[dict]:
    """Fetch recent topics from a Spiceworks Discourse category."""
    cid = CATEGORY_IDS.get(category)
    if cid is None:
        logger.error("Unknown Spiceworks category slug: %s", category)
        return []

    listing = get_json(f"{BASE}/c/{category}/{cid}.json?page=0")
    if not listing:
        return []

    topics = listing.get("topic_list", {}).get("topics", [])
    cap = config.ITEMS_PER_CATEGORY.get("spiceworks", 30)

    items = []
    for t in topics:
        if len(items) >= cap:
            break
        if t.get("pinned"):  # skip "About this category" and similar
            continue

        topic = get_json(f"{BASE}/t/{t['id']}.json")
        if not topic:
            continue

        posts = topic.get("post_stream", {}).get("posts", [])
        if not posts:
            continue

        op_body = clean_html(posts[0].get("cooked", ""))
        comments = [clean_html(p.get("cooked", "")) for p in posts[1:4]]

        items.append({
            "source": "spiceworks",
            "id": str(t["id"]),
            "category": category,
            "title": t.get("title", ""),
            "body": op_body,
            "url": f"{BASE}/t/{t.get('slug', '')}/{t['id']}",
            # No upvote analog; reply_count is the closest engagement signal
            # and the keyword filter's `score > 1` gate uses it.
            "score": t.get("reply_count", 0),
            "created_utc": _parse_iso(t.get("created_at", "")),
            "top_comments": comments,
        })

    return items


def _parse_iso(s: str) -> float:
    if not s:
        return 0.0
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
