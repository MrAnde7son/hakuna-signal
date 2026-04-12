"""Gartner Peer Insights: pre-crawled vendor reviews.

Unlike other sources that fetch live data via HTTP, Gartner reviews are crawled
locally with Playwright (gartner_crawler.py) because the site is behind
Cloudflare and blocks headless requests. The crawler dumps structured JSON to
gartner_dump/, which this module reads at pipeline time.

In production the crawled snapshot is uploaded to GCS alongside other state
files and downloaded before the pipeline run.

Data quality notes:
  - The crawler's HTML parser often fails to extract review body text, so many
    reviews only have a title. We still ingest them — the title alone carries
    sentiment signal ("Clear Alerts, Low manual effort, helpful") and the
    scorer can extract product-level pain points.
  - Pagination produces duplicate reviews (same title on consecutive pages).
    We deduplicate by (title, date) within each product.
"""

import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DUMP_DIR = PROJECT_ROOT / "gartner_dump"

# Map pipeline categories → Gartner market slugs in the dump directory.
# Multiple pipeline categories can map to the same dump market.
CATEGORY_MARKETS = {
    "vulnerability-assessment": ["vulnerability-assessment"],
    "exposure-management": ["exposure-assessment-platforms"],
    "attack-surface-management": [
        "cyber-asset-attack-surface-management",
        "external-attack-surface-management",
    ],
}


def fetch_items(category: str) -> list[dict]:
    """Load pre-crawled Gartner reviews for the given category."""
    market_slugs = CATEGORY_MARKETS.get(category)
    if not market_slugs:
        logger.error("Unknown Gartner category: %s", category)
        return []

    items = []
    for market in market_slugs:
        items.extend(_load_market_reviews(market, category))

    return items


def _load_market_reviews(market: str, category: str) -> list[dict]:
    market_dir = DUMP_DIR / "markets" / market / "vendors"
    if not market_dir.is_dir():
        logger.warning("Gartner dump not found: %s", market_dir)
        return []

    items = []
    for product_file in market_dir.glob("*/product.json"):
        try:
            data = json.loads(product_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to read %s: %s", product_file, e)
            continue

        product_name = data.get("product", "")
        slug = data.get("slug", "")
        vendor = data.get("vendor", "")
        url = data.get("url", "")

        # Deduplicate reviews by (title, date)
        seen = set()
        for review in data.get("reviews", []):
            title = review.get("title", "").strip().strip('"').strip('\u201c\u201d')
            date = review.get("date", "")
            dedup_key = (title, date)
            if not title or dedup_key in seen:
                continue
            seen.add(dedup_key)

            reviewer = review.get("reviewer", "")
            rating = review.get("rating", 0)
            body = review.get("body", "")

            # Build a meaningful title line
            title_parts = [f"{product_name} review"]
            if vendor and vendor != product_name:
                title_parts[0] = f"{product_name} ({vendor}) review"
            if reviewer:
                title_parts.append(reviewer)
            title_parts.append(f'"{title}"')
            item_title = " — ".join(title_parts)

            # Body falls back to title if the crawler didn't extract text
            item_body = body if body else title

            items.append({
                "source": "gartner",
                "id": f"gartner_{slug}_{hash(dedup_key) & 0xFFFFFFFF:08x}",
                "category": category,
                "title": item_title,
                "body": item_body,
                "url": url,
                "score": rating,
                "created_utc": _parse_date(date),
                "top_comments": [],
            })

    return items


def _parse_date(date_str: str) -> float:
    """Parse Gartner date strings like 'Nov 20, 2025' to POSIX timestamp."""
    if not date_str:
        return time.time()
    import datetime
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(date_str, fmt).timestamp()
        except ValueError:
            continue
    return time.time()
