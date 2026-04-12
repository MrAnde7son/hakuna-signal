"""ServiceNow Community: Khoros-based forum (servicenow.com/community).

Older Khoros skin than Tenable — uses <article class="custom-message-tile">
blocks with plain <h3>/<p>/<time> elements instead of data-testid markers.
The Security Operations forum covers vulnerability response, exposure
management, and SecOps integrations (Qualys, Tenable, Wiz, etc.).
"""

import logging
import re
from datetime import datetime, timezone

from ._http import clean_html, get_html

logger = logging.getLogger(__name__)

BASE = "https://www.servicenow.com"

BOARD_PATHS = {
    "secops-forum": "/community/security-operations-forum/bd-p/security-operations-forum",
}

# Each listing item is an <article class="custom-message-tile">.
_BLOCK_SPLIT_RE = re.compile(r'<article\b[^>]*class="[^"]*custom-message-tile[^"]*"', re.IGNORECASE)
_TITLE_LINK_RE = re.compile(
    r'<h3>\s*<a\s+href="(/community/secops-forum/[^"]+/td-p/(\d+))"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_BODY_RE = re.compile(r'<p>\s*(.*?)\s*</p>', re.DOTALL)
_TIME_RE = re.compile(
    r'<time[^>]*>\s*(\d{2}-\d{2}-\d{4})\s+(\d+:\d+:\d+ [AP]M)\s*</time>',
    re.DOTALL,
)


def fetch_items(category: str) -> list[dict]:
    """Fetch recent topics from a ServiceNow Community forum board."""
    path = BOARD_PATHS.get(category)
    if path is None:
        logger.error("Unknown ServiceNow Community board: %s", category)
        return []

    html = get_html(BASE + path)
    if not html:
        return []

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

        time_match = _TIME_RE.search(seg)
        created_utc = (
            _parse_time(f"{time_match.group(1)} {time_match.group(2)}")
            if time_match
            else 0.0
        )

        items.append({
            "source": "servicenow",
            "id": f"servicenow_{topic_id}",
            "category": category,
            "title": title,
            "body": body,
            "url": BASE + href,
            "score": 0,
            "created_utc": created_utc,
            "top_comments": [],
        })

    return items


def _parse_time(s: str) -> float:
    """Parse '02-10-2026 3:36:19 AM' (ServiceNow Khoros timestamp format)."""
    try:
        dt = datetime.strptime(s, "%m-%d-%Y %I:%M:%S %p")
        return dt.replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0
