import os
from dotenv import load_dotenv

load_dotenv()

# User-Agent sent on every outbound HTTP request (Reddit, Spiceworks, Tenable
# Community, PeerSpot, G2). Reddit is the only source that strictly requires
# a descriptive UA, but we use the same one everywhere for politeness.
HAKUNA_SIGNAL_USER_AGENT = os.getenv("HAKUNA_SIGNAL_USER_AGENT", "hakuna-signal/1.0")

# Vertex AI (Claude via GCP)
GCP_PROJECT = os.getenv("GCP_PROJECT", "hakuna-prod-2026")
GCP_REGION = os.getenv("GCP_REGION", "us-east5")
MODEL = "gemini-2.5-flash"

# Pipeline settings
RUN_INTERVAL_MINUTES = int(os.getenv("RUN_INTERVAL_MINUTES", "30"))
MIN_RELEVANCE_SCORE = int(os.getenv("MIN_RELEVANCE_SCORE", "7"))

SOURCES = {
    "reddit": [
        "sysadmin",
        "nessus",
        "tenable",
        "cybersecurity",
        "AskNetsec",
        "netsec",
        "qualys",
        "crowdstrike",
        "ciso",
    ],
    "spiceworks": [  # Discourse category slugs
        "security",
        "vendors",
    ],
    "tenable": [  # Khoros board slugs (community.tenable.com)
        "vulnerability-watch",
        "tenable-research-release-highlights",
        "product-announcements",
    ],
    "peerspot": [  # peerspot.com category slugs
        "vulnerability-management",
        "patch-management",
    ],
    "g2": [  # market segments — fan out to per-product RSS feeds in sources/g2.py
        "vulnerability-management",
        "exposure-and-asset-management",
        "appsec-and-cloud",
    ],
    "hackernews": [  # Algolia search queries — see sources/hackernews.CATEGORY_QUERIES
        "vulnerability-scanner",
        "vulnerability-management",
        "attack-surface",
        "exposure-management",
        "patch-management",
        "asset-discovery",
    ],
    "stackexchange": [  # Stack Exchange site names; SO is excluded as too noisy
        "security",
        "serverfault",
    ],
    "github": [  # owner/repo specs — issues from VM/asset/scanner OSS projects
        "projectdiscovery/nuclei",
        "zaproxy/zaproxy",
        "greenbone/openvas-scanner",
        "osquery/osquery",
        # Exposure management platforms (AEV / CTEM)
        "openaev-platform/openaev",
        "GitHubSecurityLab/seclab-taskflow-agent",
        # Attack Surface Management & discovery
        "projectdiscovery/subfinder",
        "owasp-amass/amass",
        "assetnote/kiterunner",
        # Vulnerability / configuration / identity validation
        "prowler-cloud/prowler",
        "SpecterOps/BloodHound",
    ],
    "rapid7": [  # discuss.rapid7.com Discourse boards
        "insightvm",
        "surface-command",
        "insightidr",
        "insightappsec",
    ],
    "servicenow": [  # servicenow.com/community Khoros boards (SecOps / exposure mgmt)
        "secops-forum",
    ],
    "gartner": [  # Gartner Peer Insights — reads pre-crawled JSON from gartner_dump/
        "vulnerability-assessment",
        "exposure-management",
        "attack-surface-management",
    ],
}

# How many items to fetch per (source, category) per run
ITEMS_PER_CATEGORY = {
    "reddit": 100,
    "spiceworks": 30,
    "tenable": 20,
    # PeerSpot is products-per-category × ~3 reviews; cap is enforced via
    # PRODUCT_CAP inside sources/peerspot.py instead.
    "peerspot": 30,
    # G2 is products-per-category × ~25 reviews per RSS feed; cap is implicit
    # in CATEGORY_PRODUCTS inside sources/g2.py.
    "g2": 250,
    "hackernews": 30,
    "stackexchange": 30,
    "github": 30,
    "rapid7": 30,
    "servicenow": 20,
    # Gartner reads from local JSON dump — no HTTP, no rate limit.
    # Cap is per-category; each market has ~20 vendors × ~10 unique reviews.
    "gartner": 200,
}
