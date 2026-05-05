"""arXiv: Atom export API at export.arxiv.org/api/query.

The user-facing listing is https://arxiv.org/list/cs.CR/recent (Cryptography and
Security). The corresponding machine-readable feed is the Atom export API,
which is sortable, paginated, and has stable IDs — no HTML scraping needed.

cs.CR is broad (academic crypto, theoretical security, ML adversarial papers,
etc.) so it's NOT in ALWAYS_PASS_CATEGORIES — the keyword filter gates it the
same way it gates security.stackexchange.com. Papers have no engagement signal
(no upvotes / replies / comments), so we set score=0; this means MEDIUM_SIGNAL
matches on non-Reddit sources won't pass the engagement>1 gate, which is the
right call — abstract papers with only medium keyword density are noise.
"""

import logging
import xml.etree.ElementTree as ET
from datetime import datetime

import config

from ._http import clean_html, get_html

logger = logging.getLogger(__name__)

API = "https://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def fetch_items(category: str) -> list[dict]:
    """Fetch recent papers in an arXiv category (e.g. 'cs.CR').

    `category` is the arXiv category code, used directly in the API
    `search_query=cat:<category>` parameter.
    """
    limit = config.ITEMS_PER_CATEGORY.get("arxiv", 50)
    url = (
        f"{API}?search_query=cat:{category}"
        f"&sortBy=submittedDate&sortOrder=descending&max_results={limit}"
    )
    body = get_html(url)
    if not body:
        return []

    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        logger.error("Failed to parse arXiv Atom feed for %s: %s", category, e)
        return []

    items = []
    for entry in root.findall("atom:entry", ATOM_NS):
        arxiv_id = _extract_id(entry)
        if not arxiv_id:
            continue

        title = clean_html(_text(entry, "atom:title"))
        summary = clean_html(_text(entry, "atom:summary"))
        if not title:
            continue

        link = _abs_link(entry) or f"https://arxiv.org/abs/{arxiv_id}"

        items.append({
            "source": "arxiv",
            "id": f"arxiv_{arxiv_id}",
            "category": category,
            "title": title,
            "body": summary,
            "url": link,
            # arXiv has no upvotes/replies. The keyword filter's engagement
            # gate is only consulted for medium-signal hits on non-Reddit
            # sources; high-signal hits pass regardless.
            "score": 0,
            "created_utc": _parse_iso(_text(entry, "atom:published")),
            # No comments on arXiv — empty list, not None (None means
            # "deferred, hydrate later" per the source contract).
            "top_comments": [],
        })

    return items


def _text(entry: ET.Element, path: str) -> str:
    el = entry.find(path, ATOM_NS)
    return (el.text or "") if el is not None else ""


def _extract_id(entry: ET.Element) -> str:
    """Pull the arXiv id out of <id>http://arxiv.org/abs/2501.12345v2</id>.

    Strip the version suffix so a v2 revision of a paper we already saw as v1
    dedups. The abs page always serves the latest version anyway.
    """
    raw = _text(entry, "atom:id")
    if not raw:
        return ""
    tail = raw.rsplit("/", 1)[-1]
    # "2501.12345v2" -> "2501.12345"; old-style "cs.CR/0501001v1" -> "cs.CR/0501001"
    if "v" in tail:
        base, _, version = tail.rpartition("v")
        if version.isdigit():
            tail = base
    return tail


def _abs_link(entry: ET.Element) -> str:
    """Prefer the rel='alternate' link (the abs page). Fall back to first link."""
    first = ""
    for link in entry.findall("atom:link", ATOM_NS):
        href = link.get("href") or ""
        if not href:
            continue
        if not first:
            first = href
        if link.get("rel") == "alternate":
            return href
    return first


def _parse_iso(s: str) -> float:
    if not s:
        return 0.0
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
