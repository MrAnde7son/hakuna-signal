"""Stack Exchange: official API at api.stackexchange.com.

Stack Exchange exposes a free, no-auth API with a 300-request/day quota per
IP. We hit `/2.3/questions` per site to get the most recent questions. The
`withbody` filter inflates the response to include the question body HTML
(otherwise we'd only get titles).

Each "category" here is a Stack Exchange site name (the API param):
  - security  -> security.stackexchange.com  (every question is on-topic)
  - serverfault -> serverfault.com  (sysadmin pain — patching, scanning)

stackoverflow.com is intentionally NOT included: the volume is enormous,
nearly all of it is unrelated to vulnerability management, and the keyword
filter would discard ~99% of it. If we want SO coverage later, do it via
tag-filtered queries (`tagged=vulnerability-scanning` etc.) instead of the
firehose.

Answers are not fetched (top_comments=[]). The question body alone carries
the buying intent / pain signal we want; answers are validation signal and
require a second API call per question that would chew through the quota.
"""

import logging

import config

from ._http import clean_html, get_json

logger = logging.getLogger(__name__)

BASE = "https://api.stackexchange.com/2.3"

# Stack Exchange site param. The API rejects unknown sites with a 400.
SITES = {
    "security",
    "serverfault",
}


def fetch_items(category: str) -> list[dict]:
    """Fetch the most recent questions from a Stack Exchange site."""
    if category not in SITES:
        logger.error("Unknown Stack Exchange site: %s", category)
        return []

    limit = config.ITEMS_PER_CATEGORY.get("stackexchange", 30)
    # `withbody` is a built-in filter that adds the question body HTML to the
    # default response. Sort by creation desc so we always see the freshest.
    url = (
        f"{BASE}/questions"
        f"?order=desc&sort=creation&site={category}"
        f"&pagesize={limit}&filter=withbody"
    )
    data = get_json(url)
    if not data:
        return []

    quota_remaining = data.get("quota_remaining")
    if quota_remaining is not None and quota_remaining < 20:
        logger.warning("Stack Exchange quota low: %s remaining", quota_remaining)

    items = []
    for q in data.get("items", []):
        qid = q.get("question_id")
        if not qid:
            continue

        title = q.get("title") or ""
        body = clean_html(q.get("body") or "")
        if not title:
            continue

        items.append({
            "source": "stackexchange",
            # Compose with site so the same question_id on different sites
            # can't collide in the dedup table.
            "id": f"se_{category}_{qid}",
            "category": category,
            "title": title,
            "body": body,
            "url": q.get("link") or "",
            # `score` on SE is upvotes minus downvotes — a real engagement
            # signal, unlike Reddit /new where everything is 1.
            "score": q.get("score") or 0,
            "created_utc": float(q.get("creation_date") or 0),
            "top_comments": [],
        })

    return items
