"""Hacker News: Algolia search API at hn.algolia.com.

HN doesn't expose a category/topic structure — everything lives in one giant
firehose. To get a useful slice we run a search query per "category". Each
category here is a topic slug that maps to a search query in CATEGORY_QUERIES.

Algolia returns the latest stories matching the query, with title, points,
comment count, author, created_at, and (for "Ask HN" / "Show HN" posts) the
self-text in `story_text`. The link in `url` is the *external* link for link
posts; we always synthesize the HN discussion permalink so the dashboard card
points at the conversation, not the underlying article.

Comments are deferred (top_comments=None) and hydrated by main.py after the
keyword filter survives — same pattern as Reddit. The hydration endpoint is
`/items/{id}` which returns the full thread tree; we pick the top three
top-level comments by points.
"""

import logging

import config

from ._http import clean_html, get_json

logger = logging.getLogger(__name__)

BASE = "https://hn.algolia.com/api/v1"
HN_ITEM = "https://news.ycombinator.com/item?id="

# Category slug -> Algolia search query. Algolia tokenizes the query and
# requires all words to match by default, so multi-word phrases work without
# explicit quoting. Keep these aligned with the keyword_filter HIGH_SIGNAL
# list — these queries set the *funnel*, the filter sets the *gate*.
CATEGORY_QUERIES = {
    "vulnerability-scanner": "vulnerability scanner",
    "vulnerability-management": "vulnerability management",
    "attack-surface": "attack surface",
    "exposure-management": "exposure management",
    "patch-management": "patch management",
    "asset-discovery": "asset discovery",
}


def fetch_items(category: str) -> list[dict]:
    """Fetch the most recent HN stories matching this category's search query."""
    query = CATEGORY_QUERIES.get(category)
    if query is None:
        logger.error("Unknown Hacker News category: %s", category)
        return []

    limit = config.ITEMS_PER_CATEGORY.get("hackernews", 30)
    # search_by_date sorts newest-first; /search would sort by relevance and
    # bury fresh items behind years-old high-point classics.
    url = f"{BASE}/search_by_date?query={query}&tags=story&hitsPerPage={limit}"
    data = get_json(url)
    if not data:
        return []

    items = []
    for hit in data.get("hits", []):
        object_id = hit.get("objectID")
        if not object_id:
            continue

        title = hit.get("title") or ""
        if not title:
            continue

        # story_text is set for Ask HN / Show HN / text posts; link posts have
        # an external `url` and no story_text. Either way, the discussion
        # permalink is item?id=N, not the external article link.
        body = clean_html(hit.get("story_text") or "")

        items.append({
            "source": "hackernews",
            "id": f"hn_{object_id}",
            "category": category,
            "title": title,
            "body": body,
            "url": f"{HN_ITEM}{object_id}",
            "score": hit.get("points") or 0,
            "created_utc": float(hit.get("created_at_i") or 0),
            # Defer comment fetch until after dedup + keyword filter, same as
            # the Reddit source. main.py dispatches to fetch_comments below.
            "top_comments": None,
        })

    return items


def fetch_comments(object_id: str) -> list[str]:
    """Fetch the top 3 top-level comments for an HN story.

    object_id is the bare numeric id (e.g. "42163591"); the source-prefixed
    form ("hn_42163591") is what we store in the dedup table, but the API
    wants the bare id.
    """
    bare = object_id.removeprefix("hn_")
    data = get_json(f"{BASE}/items/{bare}")
    if not data:
        return []

    children = data.get("children") or []
    # Top-level comments only; sort by points descending. Algolia returns
    # `points` as None for comments where the score is hidden, so coerce.
    ranked = sorted(
        (c for c in children if c.get("text")),
        key=lambda c: (c.get("points") or 0),
        reverse=True,
    )
    return [clean_html(c.get("text") or "") for c in ranked[:3]]
