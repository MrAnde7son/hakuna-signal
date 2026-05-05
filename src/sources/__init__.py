"""Source registry. Each source module exposes `fetch_items(category) -> list[dict]`.

Items are uniformly shaped:
    {
      "source": str,        # e.g. "reddit", "spiceworks"
      "id": str,            # source-unique id (Reddit base36, Spiceworks numeric)
      "category": str,      # subreddit name, Discourse category slug, etc.
      "title": str,
      "body": str,          # plain text
      "url": str,
      "score": int,         # engagement signal (upvotes / reply count)
      "created_utc": float, # POSIX timestamp
      "top_comments": list[str] | None,  # None = deferred; hydrate after filter
    }
"""

import config

from . import reddit, spiceworks, tenable, peerspot, g2, hackernews, stackexchange, github, rapid7, servicenow, gartner, arxiv

REGISTRY = {
    "reddit": reddit,
    "spiceworks": spiceworks,
    "tenable": tenable,
    "peerspot": peerspot,
    "g2": g2,
    "hackernews": hackernews,
    "stackexchange": stackexchange,
    "github": github,
    "rapid7": rapid7,
    "servicenow": servicenow,
    "gartner": gartner,
    "arxiv": arxiv,
}


def iter_enabled_sources():
    """Yield (source_name, category, fetch_callable) for every configured source/category."""
    for source_name, categories in config.SOURCES.items():
        module = REGISTRY.get(source_name)
        if module is None:
            continue
        for category in categories:
            yield source_name, category, module.fetch_items
