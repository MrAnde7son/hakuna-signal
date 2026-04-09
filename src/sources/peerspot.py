"""PeerSpot: software review site (peerspot.com).

Server-rendered HTML, no public API. Each product page exposes 3 review
"snippets" anonymously — the rest are gated behind a JS modal we can't reach
without auth. The 3 visible snippets are substantial (~500–1500 chars each),
include star ratings and reviewer titles, and are exactly the kind of "what
do real practitioners think about Tenable/Qualys/Rapid7" intel we want.

Per (source, category) call we:
  1. Fetch /categories/{category}, extract product slugs (up to PRODUCT_CAP)
  2. Fetch each /products/{slug}-reviews and extract its 3 review snippets
That's PRODUCT_CAP+1 HTTP requests per category. With 8 products and a 3s
politeness floor, ~27s per category run.

Reviews don't expose dates in the listing HTML, so created_utc falls back to
the time we first ingested the review (best effort — for review data the
"posted" date is far less useful than for forum threads anyway).
"""

import logging
import re
import time

from ._http import clean_html, get_html

logger = logging.getLogger(__name__)

BASE = "https://www.peerspot.com"

# How many product slugs to pull per category. Each product yields ~3 reviews.
PRODUCT_CAP = 8

_PRODUCT_LINK_RE = re.compile(r'href="/products/([a-z0-9-]+)-reviews"')
_REVIEW_CARD_SPLIT_RE = re.compile(r'<div[^>]*class="review-card[^"]*"[^>]*>')
_REVIEW_ID_RE = re.compile(r'data-select-review="(\d+)"')
_SNIPPET_RE = re.compile(
    r'<div class="snippet[^"]*">.*?<span>(.*?)</span>',
    re.DOTALL,
)
_RATING_BLOCK_RE = re.compile(r'<div class="rating">(.*?)</div>', re.DOTALL)
_REVIEWER_NAME_RE = re.compile(
    r'<a[^>]*href="/users/[^"]+"[^>]*><span>([^<]+)</span></a>'
)
_REVIEWER_TITLE_RE = re.compile(r'<div class="info">([^<]+)</div>')


def fetch_items(category: str) -> list[dict]:
    """Fetch the top reviews for the top products in a PeerSpot category."""
    cat_html = get_html(f"{BASE}/categories/{category}")
    if not cat_html:
        return []

    # Dedup product slugs in order of first appearance
    seen = set()
    slugs = []
    for slug in _PRODUCT_LINK_RE.findall(cat_html):
        if slug in seen:
            continue
        seen.add(slug)
        slugs.append(slug)
        if len(slugs) >= PRODUCT_CAP:
            break

    items = []
    for slug in slugs:
        prod_html = get_html(f"{BASE}/products/{slug}-reviews")
        if not prod_html:
            continue
        product_label = slug.replace("-", " ").title()

        chunks = _REVIEW_CARD_SPLIT_RE.split(prod_html)[1:]
        for chunk in chunks:
            review_id_m = _REVIEW_ID_RE.search(chunk)
            snippet_m = _SNIPPET_RE.search(chunk)
            if not review_id_m or not snippet_m:
                continue

            review_id = review_id_m.group(1)
            body = clean_html(snippet_m.group(1))
            if not body:
                continue

            stars = _parse_rating(chunk)
            reviewer_name = _first(_REVIEWER_NAME_RE.search(chunk))
            reviewer_title = _first(_REVIEWER_TITLE_RE.search(chunk))
            byline = " — ".join(p for p in [reviewer_name, reviewer_title] if p)

            items.append({
                "source": "peerspot",
                "id": f"peerspot_{review_id}",
                "category": category,
                # Title combines product + reviewer so the dashboard card is
                # useful even before you click through.
                "title": f"{product_label} review" + (f" — {byline}" if byline else ""),
                "body": body,
                "url": f"{BASE}/products/{slug}-reviews?review_id={review_id}",
                # Star rating (1.0–5.0) used as the engagement signal. The
                # ALWAYS_PASS gate handles the keyword filter for this category.
                "score": stars,
                "created_utc": time.time(),
                "top_comments": [],
            })

    return items


def _parse_rating(chunk: str) -> float:
    """Read star icons in the rating block and return a float (0.0–5.0)."""
    rb = _RATING_BLOCK_RE.search(chunk)
    if not rb:
        return 0.0
    block = rb.group(1)
    full = len(re.findall(r"fa-star(?!-half)", block))
    half = len(re.findall(r"fa-star-half", block))
    return float(full) + (0.5 * half)


def _first(match) -> str:
    return clean_html(match.group(1)) if match else ""
