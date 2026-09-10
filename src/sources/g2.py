"""G2: software review site (g2.com).

The full HTML site is behind Cloudflare and returns 403 to anonymous fetchers,
but G2 publishes a public RSS feed at /products/{slug}/reviews.rss with no
authentication required. Each feed returns ~25 of the most recent reviews
with a clean, structured payload:

  - title    : the review's headline
  - link     : permalink to the review
  - pubDate  : RFC 822 timestamp
  - description : HTML containing reviewer name, star rating, role, industry,
                  company size, and three free-text Q&A blocks
                  ("What do you like best...?", "What do you dislike...?",
                  "What problems is X solving and how is that benefiting you?")

This structure is denser than what we get from PeerSpot — the company profile
fields (role / industry / size) are exactly what the scorer prompt's
COMPANY PROFILE extraction wants.

Each (source, category) call fans out to N RSS feeds (one per product) at the
shared 3-second politeness floor. Keep PRODUCTS_PER_CATEGORY modest to avoid
multi-minute fetch times.
"""

import logging
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from ._http import clean_html, get_html

logger = logging.getLogger(__name__)

BASE = "https://www.g2.com"

# Curated product slugs per market segment. All slugs were verified to return
# HTTP 200 from /products/{slug}/reviews.rss during planning. Add more as
# needed — slugs are *not* always the obvious dasherized name (e.g. it's
# `tenable-vulnerability-management`, not `tenable-vm`; `crowdstrike-falcon`
# works but `crowdstrike-falcon-spotlight` does not). Verify with curl before
# adding.
CATEGORY_PRODUCTS = {
    "vulnerability-management": [
        "tenable-nessus",
        "tenable-vulnerability-management",
        "qualys-vmdr",
        "insightvm",
        "rapid7",
        "microsoft-defender-vulnerability-management",
        "tanium",
        "crowdstrike-falcon",
        "trend-vision-one",
    ],
    "exposure-and-asset-management": [
        "tenable-asm",
        "cycognito",
        "runzero",
        "censys",
        "shodan",
        "jupiterone",
        "axonius",
        "armis",
    ],
    "appsec-and-cloud": [
        "snyk",
        "veracode",
        "metasploit",
        "lacework",
        "orca-security",
        "nucleus-security",
        "brinqa",
    ],
    # Endpoint / patch / configuration management competitors. Slugs verified
    # to 200 on /products/{slug}/reviews.rss (Sept 2026). Note the non-obvious
    # ones: it's `tanium` (not `tanium-platform`), `jamf` (not `jamf-pro`),
    # `pdq-deploy-inventory` (not `pdq-deploy`), and Intune's review feed lives
    # under `microsoft-intune-enterprise-application-management`.
    "endpoint-and-patch-management": [
        "ninjaone",
        "automox",
        "action1",
        "ivanti-neurons-for-patch-management",
        "manageengine-endpoint-central",
        "tanium",
        "microsoft-intune-enterprise-application-management",
        "pdq-deploy-inventory",
        "pdq-connect",
        "heimdal-patch-management",
        "jumpcloud",
        "jamf",
    ],
}

# RSS namespace handling: G2's feed uses no namespace, plain RSS 2.0.
_REVIEWER_RE = re.compile(r"Review from\s+([^<\n]+?)\s*</p>", re.IGNORECASE)
_STARS_RE = re.compile(r"(\d(?:\.\d)?)\s*Stars", re.IGNORECASE)
_REVIEW_ID_RE = re.compile(r"-review-(\d+)$")


def fetch_items(category: str) -> list[dict]:
    """Fetch the latest G2 reviews for every product in this category."""
    slugs = CATEGORY_PRODUCTS.get(category)
    if not slugs:
        logger.error("Unknown G2 category: %s", category)
        return []

    items = []
    for slug in slugs:
        rss_text = get_html(f"{BASE}/products/{slug}/reviews.rss")
        if not rss_text:
            continue
        items.extend(_parse_feed(rss_text, slug, category))

    return items


def _parse_feed(rss_text: str, slug: str, category: str) -> list[dict]:
    try:
        root = ET.fromstring(rss_text)
    except ET.ParseError as e:
        logger.error("G2 RSS parse error for %s: %s", slug, e)
        return []

    channel = root.find("channel")
    if channel is None:
        return []

    # Channel title is "X Reviews" — strip the suffix to get the product label
    channel_title = (channel.findtext("title") or "").strip()
    product_label = channel_title.removesuffix(" Reviews").strip() or _slug_to_label(slug)

    out = []
    for item in channel.findall("item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue

        review_id_m = _REVIEW_ID_RE.search(link)
        if not review_id_m:
            # No parseable review id — skip rather than risk dedup collisions
            continue
        review_id = review_id_m.group(1)

        review_title = clean_html(item.findtext("title") or "")
        description_html = item.findtext("description") or ""

        reviewer = _extract_reviewer(description_html)
        stars = _extract_stars(description_html)
        body = clean_html(description_html)
        if not body:
            continue

        # Card title combines product, reviewer headline, and the review title
        # so each card is meaningful on its own in the dashboard.
        title_parts = [f"{product_label} review"]
        if reviewer:
            title_parts.append(reviewer)
        if review_title:
            title_parts.append(f'"{review_title}"')
        title = " — ".join(title_parts)

        pub_date = item.findtext("pubDate") or ""
        created_utc = _parse_pubdate(pub_date)

        out.append({
            "source": "g2",
            "id": f"g2_{review_id}",
            "category": category,
            "title": title,
            "body": body,
            "url": link,
            "score": stars,
            "created_utc": created_utc,
            "top_comments": [],
        })

    return out


def _extract_reviewer(description_html: str) -> str:
    m = _REVIEWER_RE.search(description_html)
    return m.group(1).strip() if m else ""


def _extract_stars(description_html: str) -> float:
    m = _STARS_RE.search(description_html)
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except ValueError:
        return 0.0


def _parse_pubdate(s: str) -> float:
    if not s:
        return 0.0
    try:
        return parsedate_to_datetime(s).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _slug_to_label(slug: str) -> str:
    return slug.replace("-", " ").title()
