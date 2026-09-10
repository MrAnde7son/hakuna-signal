"""Gartner Peer Insights competitive landscape.

Loads pre-crawled vendor data from gartner_dump/ and builds:

1. A name→vendor lookup used to enrich tools_detected in the dashboard
   (e.g. when a thread mentions "Qualys", show its Gartner rating).

2. A per-market landscape summary served via /api/landscape for the
   dashboard's Competitive Landscape view.

The processed snapshot is saved to reports/gartner_landscape.json so the
dashboard server can load it without re-scanning the dump directory each
time. In production, this file is synced to GCS alongside data.json.
"""

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DUMP_DIR = PROJECT_ROOT / "gartner_dump"
LANDSCAPE_FILE = PROJECT_ROOT / "reports" / "gartner_landscape.json"

# Aliases: map common tool names seen in scorer output → Gartner product names.
# The scorer may detect "Nessus" but Gartner lists "Tenable Nessus".
_ALIASES = {
    "nessus": "Tenable Nessus",
    "tenable.io": "Tenable Vulnerability Management",
    "tenable.sc": "Tenable Security Center",
    "insightvm": "InsightVM",
    "nexpose": "InsightVM",
    "qualys vmdr": "Qualys VMDR",
    "crowdstrike falcon": "CrowdStrike Falcon Exposure Management",
    # Endpoint / patch / configuration management (populated once the
    # unified-endpoint-management-tools market is crawled into gartner_dump/)
    "intune": "Microsoft Intune",
    "microsoft intune": "Microsoft Intune",
    "sccm": "Microsoft Configuration Manager",
    "mecm": "Microsoft Configuration Manager",
    "endpoint central": "ManageEngine Endpoint Central",
    "manageengine": "ManageEngine Endpoint Central",
    "ninja one": "NinjaOne",
    "action1": "Action1",
    "ivanti neurons": "Ivanti Neurons for MDM",
    "kace": "Quest KACE",
}


def load_landscape() -> dict:
    """Load the cached landscape JSON, or rebuild from dump if missing."""
    if LANDSCAPE_FILE.exists():
        try:
            return json.loads(LANDSCAPE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupt landscape file — rebuilding")

    return build_landscape()


def build_landscape() -> dict:
    """Scan gartner_dump/ and build the landscape data structure."""
    markets = {}
    vendor_lookup = {}  # normalized_name → vendor summary

    if not DUMP_DIR.is_dir():
        logger.warning("Gartner dump directory not found: %s", DUMP_DIR)
        return {"markets": {}, "vendors": {}}

    for market_dir in sorted((DUMP_DIR / "markets").iterdir()):
        if not market_dir.is_dir():
            continue
        market_slug = market_dir.name
        vendors_dir = market_dir / "vendors"
        if not vendors_dir.is_dir():
            continue

        market_vendors = []
        for product_file in sorted(vendors_dir.glob("*/product.json")):
            try:
                data = json.loads(product_file.read_text())
            except (json.JSONDecodeError, OSError):
                continue

            # Deduplicate reviews to get accurate count
            seen_titles = set()
            unique_reviews = 0
            for r in data.get("reviews", []):
                title = r.get("title", "").strip().strip('"').strip('\u201c\u201d')
                date = r.get("date", "")
                key = (title, date)
                if title and key not in seen_titles:
                    seen_titles.add(key)
                    unique_reviews += 1

            vendor_info = {
                "product": data.get("product", ""),
                "vendor": data.get("vendor", ""),
                "slug": data.get("slug", ""),
                "rating": data.get("rating"),
                "review_count": data.get("review_count"),
                "unique_reviews_crawled": unique_reviews,
                "description": data.get("description", ""),
                "url": data.get("url", ""),
                "market": market_slug,
            }
            market_vendors.append(vendor_info)

            # Index by both product name and vendor name (lowercased)
            for name in {data.get("product", ""), data.get("vendor", "")}:
                if name:
                    vendor_lookup[name.lower()] = vendor_info

        markets[market_slug] = {
            "slug": market_slug,
            "label": market_slug.replace("-", " ").title(),
            "vendor_count": len(market_vendors),
            "vendors": sorted(market_vendors,
                              key=lambda v: v.get("rating") or 0,
                              reverse=True),
        }

    # Add aliases
    for alias, canonical in _ALIASES.items():
        canonical_lower = canonical.lower()
        if canonical_lower in vendor_lookup and alias not in vendor_lookup:
            vendor_lookup[alias] = vendor_lookup[canonical_lower]

    landscape = {"markets": markets, "vendors": vendor_lookup}

    # Persist
    try:
        LANDSCAPE_FILE.parent.mkdir(parents=True, exist_ok=True)
        LANDSCAPE_FILE.write_text(json.dumps(landscape, indent=2, ensure_ascii=False))
        logger.info("Landscape written to %s (%d vendors across %d markets)",
                     LANDSCAPE_FILE, len(vendor_lookup), len(markets))
    except OSError as e:
        logger.error("Failed to write landscape file: %s", e)

    return landscape


def enrich_tools(tools_detected: list[str], landscape: dict | None = None) -> list[dict]:
    """Given a list of tool names from the scorer, return enriched entries.

    Each entry has the original tool name plus Gartner metadata (if found):
    {"name": "Qualys", "gartner": {"rating": 4.3, "review_count": 120, ...}}
    """
    if landscape is None:
        landscape = load_landscape()
    vendors = landscape.get("vendors", {})

    enriched = []
    for tool in tools_detected:
        entry = {"name": tool}
        match = vendors.get(tool.lower())
        if match:
            entry["gartner"] = {
                "product": match["product"],
                "vendor": match["vendor"],
                "rating": match.get("rating"),
                "review_count": match.get("review_count"),
                "market": match.get("market", ""),
            }
        enriched.append(entry)
    return enriched
