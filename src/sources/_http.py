"""Shared rate-limited HTTP client used by all source fetchers."""

import html
import logging
import re
import time

import requests

import config

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_html(s: str) -> str:
    """Strip HTML tags, unescape entities, collapse whitespace."""
    if not s:
        return ""
    text = _TAG_RE.sub(" ", s)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": config.HAKUNA_SIGNAL_USER_AGENT,
})

# Politeness floor between requests across all sources sharing this session
_MIN_INTERVAL = 3.0
_last_request_time = 0.0


def _rate_limit():
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _last_request_time = time.time()


def get_json(url: str) -> dict | list | None:
    """Fetch a JSON URL with a global request-rate floor. Returns None on failure."""
    _rate_limit()
    try:
        resp = SESSION.get(url, timeout=15, headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.error("Failed to fetch %s: %s", url, e)
        return None


def get_html(url: str) -> str | None:
    """Fetch a URL and return the response body as text. Returns None on failure.

    Used by sources that don't expose a public JSON API (Tenable Community,
    PeerSpot) and require server-rendered HTML scraping.
    """
    _rate_limit()
    try:
        resp = SESSION.get(url, timeout=20, headers={
            # Some sites (PeerSpot) gate on a real-browser Accept header
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as e:
        logger.error("Failed to fetch %s: %s", url, e)
        return None
