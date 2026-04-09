"""Rapid7 Discuss: Discourse-based forum at discuss.rapid7.com.

Same Discourse API shape as Spiceworks — see sources/spiceworks.py for the
endpoint reference. The two modules are deliberately kept separate rather
than abstracted into a shared `_discourse.py` helper: only two Discourse
communities exist in the source set, and the categories / engagement
semantics differ enough per-community that early abstraction would obscure
more than it saves. Refactor when a third Discourse instance shows up.

Why Rapid7 specifically (and not Qualys / CrowdStrike, the other two vendor
communities we considered):

  - Qualys (success.qualys.com) is a Salesforce Lightning / Aura app. The
    HTML shell is ~250kB of empty divs and the real content loads via JS.
    Scraping it requires a headless browser — out of scope for this tier.
  - CrowdStrike (community.crowdstrike.com) redirects anonymous traffic to
    /private/login. There's no public read tier; you need a Falcon customer
    account. Out of reach without auth.
  - Rapid7 (discuss.rapid7.com) is plain Discourse, public read, no auth.

The strongest signal lives in the `insightvm` board (1,300+ topics) — it's
their flagship VM product, so customers complain there about scan accuracy,
asset discovery gaps, ticketing integrations, etc. `surface-command` is
their EASM product (~15 topics; emerging signal). The InsightIDR/AppSec
boards are adjacent and useful for breadth.
"""

import logging
from datetime import datetime

import config

from ._http import clean_html, get_json

logger = logging.getLogger(__name__)

BASE = "https://discuss.rapid7.com"

# Slug -> Discourse category id, resolved from /categories.json during
# planning. Refresh with:
#   curl -s https://discuss.rapid7.com/categories.json | jq '.category_list.categories[] | {slug, id}'
CATEGORY_IDS = {
    "insightvm": 13,
    "surface-command": 47,
    "insightidr": 12,
    "insightappsec": 14,
}


def fetch_items(category: str) -> list[dict]:
    """Fetch recent topics from a Rapid7 Discourse category."""
    cid = CATEGORY_IDS.get(category)
    if cid is None:
        logger.error("Unknown Rapid7 category slug: %s", category)
        return []

    listing = get_json(f"{BASE}/c/{category}/{cid}.json?page=0")
    if not listing:
        return []

    topics = listing.get("topic_list", {}).get("topics", [])
    cap = config.ITEMS_PER_CATEGORY.get("rapid7", 30)

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
            "source": "rapid7",
            "id": str(t["id"]),
            "category": category,
            "title": t.get("title", ""),
            "body": op_body,
            "url": f"{BASE}/t/{t.get('slug', '')}/{t['id']}",
            # reply_count is the closest engagement analog (no upvotes).
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
