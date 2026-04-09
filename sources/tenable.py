"""Tenable Community: Khoros-based forum (community.tenable.com).

The Khoros REST API (LiQL) is enabled but requires auth — anonymous requests
return 403. We scrape the server-rendered HTML listing pages instead. Each
listing page already includes the title, a 3-line body preview (clamped via
the `lia-g-clamp-3` CSS class), the post timestamp, and the topic URL — so
one HTTP request per board yields ~10–15 items with no per-topic round-trips.

Tenable Community is heavily vendor-curated. The signal is mostly:
  - vulnerability-watch: Tenable Research's CVE writeups (timely intel)
  - tenable-research-release-highlights: plugin/research releases
  - product-announcements: roadmap and release notes
There is no general user-discussion board (the Support category is just KB
articles), so don't expect Reddit-style practitioner pain here. The value is
competitive/intel signal on what Tenable is publishing.
"""

import logging
import re
from datetime import datetime, timezone

from ._http import clean_html, get_html

logger = logging.getLogger(__name__)

BASE = "https://community.tenable.com"

# Discussion board paths under category/news-you-need. The /category/support
# branch only contains KB articles (no discussion threads), so it's not listed.
BOARD_PATHS = {
    "vulnerability-watch": "/category/news-you-need/discussions/vulnerability-watch",
    "tenable-research-release-highlights": "/category/news-you-need/discussions/tenable-research-release-highlights",
    "product-announcements": "/category/news-you-need/discussions/product-announcements",
}

# Each listing item is rendered as a div with data-testid="InlineMessageView".
# We split the listing on these markers and parse each chunk independently.
_BLOCK_SPLIT_RE = re.compile(r'<div[^>]*data-testid="InlineMessageView"', re.IGNORECASE)
_TITLE_LINK_RE = re.compile(
    r'<a[^>]*data-testid="MessageLink"[^>]*href="(/discussions/[^"]+/(\d+))"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_BODY_RE = re.compile(
    r'class="[^"]*lia-g-message-body[^"]*"[^>]*>(.*?)</div>',
    re.DOTALL,
)
_TIME_TITLE_RE = re.compile(
    r'title="(\w+ \d+, \d{4} at \d+:\d+ [AP]M)"'
)


def fetch_items(category: str) -> list[dict]:
    """Fetch recent topics from a Tenable Community discussion board."""
    path = BOARD_PATHS.get(category)
    if path is None:
        logger.error("Unknown Tenable Community board: %s", category)
        return []

    html = get_html(BASE + path)
    if not html:
        return []

    # Split on the InlineMessageView marker. The first segment is the page
    # header (no item) — drop it.
    segments = _BLOCK_SPLIT_RE.split(html)[1:]

    items = []
    for seg in segments:
        title_link = _TITLE_LINK_RE.search(seg)
        if not title_link:
            continue
        href, topic_id, title_html = title_link.groups()
        title = clean_html(title_html)
        if not title:
            continue

        body_match = _BODY_RE.search(seg)
        body = clean_html(body_match.group(1)) if body_match else ""

        time_match = _TIME_TITLE_RE.search(seg)
        created_utc = _parse_human_time(time_match.group(1)) if time_match else 0.0

        items.append({
            "source": "tenable",
            "id": f"tenable_{topic_id}",
            "category": category,
            "title": title,
            "body": body,
            "url": BASE + href,
            # No upvote/reaction signal exposed in the listing markup.
            "score": 0,
            "created_utc": created_utc,
            "top_comments": [],
        })

    return items


def _parse_human_time(s: str) -> float:
    """Parse 'October 15, 2025 at 11:44 AM' (the format Tenable uses in title attrs)."""
    try:
        dt = datetime.strptime(s, "%B %d, %Y at %I:%M %p")
        return dt.replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0
