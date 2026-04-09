"""GitHub Issues: REST API at api.github.com.

We pull recent issues from a curated list of vulnerability-management /
asset-discovery / patch-management OSS repos. The `category` is the full
"owner/repo" spec — that's what gets passed to the GitHub API and what shows
up as the category label in the dashboard.

Unauthenticated requests are rate-limited to 60/hour per IP. With ~5 repos
× 1 request/repo × 2 runs/hour, we use ~10/hour and stay well under. If
GITHUB_TOKEN is set, we use it and lift the ceiling to 5,000/hour.

Two filters applied at fetch time:

  1. Pull requests are excluded. The `/issues` endpoint returns BOTH issues
     and PRs; PRs have a `pull_request` key on the response object. We don't
     want vendor PR firehose noise (dependabot, renovate, etc.) in the
     pipeline — only real user issues.

  2. Bot users are excluded. GitHub marks them with `user.type == "Bot"`,
     and the convention is also a `[bot]` suffix in the login. Filtering
     these removes ~half the noise on active repos like nuclei.

Comments are not fetched (top_comments=[]); the issue body usually contains
the full pain description for VM/OSS-tool issues. If we want comments later,
they live at /repos/{owner}/{repo}/issues/{number}/comments.
"""

import logging
import os
from datetime import datetime

import requests

import config

from ._http import SESSION, _rate_limit, clean_html

logger = logging.getLogger(__name__)

BASE = "https://api.github.com"

# Optional auth token for higher rate limit. Set GITHUB_TOKEN in env to lift
# from 60/hr to 5,000/hr. Read at module import — config changes require a
# process restart, same as the rest of the codebase.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()


def fetch_items(category: str) -> list[dict]:
    """Fetch recent open issues from `owner/repo`. PRs and bot issues filtered out."""
    if "/" not in category:
        logger.error("GitHub category must be 'owner/repo', got: %s", category)
        return []
    owner, repo = category.split("/", 1)

    limit = config.ITEMS_PER_CATEGORY.get("github", 30)
    url = (
        f"{BASE}/repos/{owner}/{repo}/issues"
        f"?state=open&sort=created&direction=desc&per_page={limit}"
    )

    data = _get_json_with_auth(url)
    if not data or not isinstance(data, list):
        return []

    items = []
    for issue in data:
        # Skip pull requests — the /issues endpoint returns both.
        if issue.get("pull_request"):
            continue

        user = issue.get("user") or {}
        login = user.get("login") or ""
        if user.get("type") == "Bot" or login.endswith("[bot]"):
            continue

        number = issue.get("number")
        title = issue.get("title") or ""
        if number is None or not title:
            continue

        # Issue bodies are markdown, not HTML. clean_html still safely strips
        # any inline HTML and collapses whitespace; it's a no-op for plain
        # markdown text.
        body = clean_html(issue.get("body") or "")

        items.append({
            "source": "github",
            "id": f"gh_{owner}_{repo}_{number}",
            "category": category,
            "title": title,
            "body": body,
            "url": issue.get("html_url") or "",
            # Comment count is the closest engagement analog. Reactions would
            # be richer but require a separate header (`Accept: application/
            # vnd.github.squirrel-girl-preview`) and aren't worth the cost.
            "score": issue.get("comments") or 0,
            "created_utc": _parse_iso(issue.get("created_at") or ""),
            "top_comments": [],
        })

    return items


def _get_json_with_auth(url: str):
    """Like _http.get_json, but adds the GitHub Auth header when available.

    GitHub requires a specific Accept header for stable JSON shapes, and we
    want to send a token if one is present in env. Reusing _http.SESSION
    keeps us inside the shared 3-second politeness floor.
    """
    _rate_limit()
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    try:
        resp = SESSION.get(url, timeout=15, headers=headers)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.error("Failed to fetch %s: %s", url, e)
        return None


def _parse_iso(s: str) -> float:
    if not s:
        return 0.0
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
