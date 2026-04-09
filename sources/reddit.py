"""Reddit source: fetches recent threads from a subreddit via Reddit's public JSON API."""

import config

from ._http import get_json


def fetch_items(category: str) -> list[dict]:
    """Fetch recent threads from a subreddit. `category` is the subreddit name.

    `top_comments` is left as None so callers can defer the per-thread comment
    fetch until after dedup and keyword filtering — see fetch_comments below.
    """
    limit = config.ITEMS_PER_CATEGORY.get("reddit", 100)
    url = f"https://www.reddit.com/r/{category}/new.json?limit={limit}"
    data = get_json(url)
    if not data:
        return []

    items = []
    for post in data.get("data", {}).get("children", []):
        p = post["data"]
        items.append({
            "source": "reddit",
            "id": p["id"],
            "category": category,
            "title": p.get("title", ""),
            "body": p.get("selftext", ""),
            "url": f"https://www.reddit.com{p.get('permalink', '')}",
            "score": p.get("score", 0),
            "created_utc": p.get("created_utc", 0),
            "top_comments": None,
        })

    return items


def fetch_comments(subreddit: str, thread_id: str) -> list[str]:
    """Fetch the top 3 comments for a thread."""
    url = f"https://www.reddit.com/r/{subreddit}/comments/{thread_id}.json?limit=3&sort=best"
    data = get_json(url)
    if not data or not isinstance(data, list) or len(data) < 2:
        return []

    comments = []
    for child in data[1].get("data", {}).get("children", []):
        body = child.get("data", {}).get("body", "")
        if body and child.get("kind") == "t1":
            comments.append(body)
        if len(comments) >= 3:
            break

    return comments
