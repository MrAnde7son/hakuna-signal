"""Cloud Run service entrypoint: serves the dashboard from GCS.

Uses the same container image as the pipeline job. The Cloud Run service
overrides the image CMD to run this file instead of cloud_entrypoint.py.

Files come from $DASHBOARD_BUCKET. We cache GCS reads in-memory for
$CACHE_SECONDS (default 60s) so a single page load doesn't fan out N GCS
GETs for dashboard.html + intel.json + data.json.

The Handler/API code is decoupled from the GCS client via an injectable
``fetch_impl`` callable installed by ``serve()``. The prod entrypoint
(``main()``) installs the GCS-backed backend; ``main.py`` reuses ``serve()``
with a local-files backend so ``python main.py`` gives you a working
dashboard at http://localhost:8080 with no GCS dependency.

In addition to static-file serving, this exposes a small JSON API used by
the Opportunities tab so the browser can paginate / filter / sort without
shipping the entire dataset down on first paint:

  GET /api/opportunities         — paginated, filtered, sorted page
  GET /api/opportunities/ids     — same filters, IDs only (for "mark all read")
  GET /api/sources               — distinct source/category list for the dropdown
  GET /api/intel                 — aggregate intel re-computed over the filtered set
                                   (used by the Market Intelligence tab's pivot mode)
"""
import json
import logging
import os
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mimetypes import guess_type
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("dashboard")

PORT = int(os.environ.get("PORT", "8080"))
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "60"))
INDEX = "dashboard.html"
DATA_BLOB = "data.json"

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# A fetch backend takes a blob/file name and returns (body, content_type) or
# None if missing. ``serve()`` installs one at startup; the Handler and API
# handlers go through the module-level ``fetch()`` dispatcher below.
FetchFn = Callable[[str], Optional[tuple[bytes, str]]]
_fetch_impl: FetchFn | None = None

# Parsed-JSON cache keyed on the id() of the bytes object returned by the
# active fetch backend. Backends are expected to return the SAME bytes object
# until the underlying source actually changes (mtime in local mode, expiry
# in GCS mode), so identity comparison lets us skip re-parsing on the hot path.
_parsed_cache: dict[str, tuple[int, object]] = {}


def fetch(name: str) -> tuple[bytes, str] | None:
    if _fetch_impl is None:
        raise RuntimeError("dashboard_server.serve() must install a fetch backend before fetch() is called")
    return _fetch_impl(name)


def fetch_parsed(name: str):
    """Fetch a JSON blob and return the parsed structure, cached across requests."""
    result = fetch(name)
    if result is None:
        return None
    body, _ = result
    cached = _parsed_cache.get(name)
    if cached and cached[0] == id(body):
        return cached[1]
    parsed = json.loads(body)
    _parsed_cache[name] = (id(body), parsed)
    return parsed


# --- filter/sort helpers ---------------------------------------------------

# Mirrors DRILL_DIMENSIONS in the dashboard JS so the same filter combinations
# behave identically server-side. Each entry: query-param -> match function
# that takes (scorer_result_dict, value) and returns bool.
DIMENSION_MATCHERS = {
    "tool":           lambda s, v: v in (s.get("tools_detected") or []),
    "pain":           lambda s, v: v in (s.get("pain_point_categories") or []),
    "function":       lambda s, v: v in (s.get("team_functions") or []),
    "industry":       lambda s, v: (s.get("company_profile") or {}).get("industry") == v,
    "employee_range": lambda s, v: (s.get("company_profile") or {}).get("employee_range") == v,
    "team_size":      lambda s, v: (s.get("company_profile") or {}).get("security_team_size") == v,
    "maturity":       lambda s, v: (s.get("company_profile") or {}).get("maturity_level") == v,
    "country":        lambda s, v: (s.get("company_profile") or {}).get("country") == v,
    "company":        lambda s, v: (s.get("company_profile") or {}).get("company_name") == v,
}


def _source_category_key(thread: dict) -> str:
    return f"{thread.get('source') or 'reddit'}/{thread.get('category') or ''}"


def _source_category_label(thread: dict) -> str:
    source = thread.get("source") or "reddit"
    cat = thread.get("category") or ""
    return f"r/{cat}" if source == "reddit" else f"{source}/{cat}"


def _first(qs: dict, key: str) -> str | None:
    """parse_qs returns lists; we only ever care about the first value."""
    vals = qs.get(key)
    return vals[0] if vals else None


def filter_items(items: list[dict], qs: dict) -> list[dict]:
    sub = _first(qs, "sub")
    min_score_raw = _first(qs, "min_score")
    date_from = _first(qs, "from")
    date_to = _first(qs, "to")

    try:
        min_score = int(min_score_raw) if min_score_raw else None
    except ValueError as e:
        raise ValueError(f"min_score must be an integer: {min_score_raw!r}") from e

    # Snapshot active dimension filters once
    dim_filters = [
        (DIMENSION_MATCHERS[k], _first(qs, k))
        for k in DIMENSION_MATCHERS
        if _first(qs, k)
    ]

    out = []
    for d in items:
        thread = d.get("thread") or {}
        scorer = d.get("scorer_result") or {}

        if sub and _source_category_key(thread) != sub:
            continue
        if min_score is not None and (scorer.get("score") or 0) < min_score:
            continue
        added_at = d.get("added_at") or ""
        if date_from and added_at[:10] < date_from:
            continue
        if date_to and added_at[:10] > date_to:
            continue
        if any(not match(scorer, v) for match, v in dim_filters):
            continue
        out.append(d)
    return out


def sort_items(items: list[dict], sort_key: str) -> list[dict]:
    # Stable sort + tuple keys avoid lambda gymnastics on missing fields.
    if sort_key == "date-desc" or not sort_key:
        return sorted(items, key=lambda d: (d.get("thread") or {}).get("created_utc") or 0, reverse=True)
    if sort_key == "date-asc":
        return sorted(items, key=lambda d: (d.get("thread") or {}).get("created_utc") or 0)
    if sort_key == "score-desc":
        return sorted(items, key=lambda d: (d.get("scorer_result") or {}).get("score") or 0, reverse=True)
    if sort_key == "score-asc":
        return sorted(items, key=lambda d: (d.get("scorer_result") or {}).get("score") or 0)
    if sort_key == "upvotes-desc":
        return sorted(items, key=lambda d: (d.get("thread") or {}).get("score") or 0, reverse=True)
    if sort_key == "upvotes-asc":
        return sorted(items, key=lambda d: (d.get("thread") or {}).get("score") or 0)
    if sort_key == "subreddit":
        return sorted(items, key=lambda d: _source_category_label(d.get("thread") or {}))
    raise ValueError(f"unknown sort key: {sort_key!r}")


def paginate(items: list[dict], page: int, page_size: int) -> tuple[list[dict], int]:
    total = len(items)
    if total == 0:
        return [], 0
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(page, total_pages))
    start = (page - 1) * page_size
    return items[start:start + page_size], total_pages


def parse_pagination(qs: dict) -> tuple[int, int]:
    page_raw = _first(qs, "page")
    size_raw = _first(qs, "page_size")
    try:
        page = int(page_raw) if page_raw else 1
    except ValueError as e:
        raise ValueError(f"page must be an integer: {page_raw!r}") from e
    try:
        page_size = int(size_raw) if size_raw else DEFAULT_PAGE_SIZE
    except ValueError as e:
        raise ValueError(f"page_size must be an integer: {size_raw!r}") from e
    page = max(1, page)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    return page, page_size


# --- API handlers ----------------------------------------------------------

def api_opportunities(qs: dict) -> tuple[int, dict]:
    items = fetch_parsed(DATA_BLOB)
    if items is None:
        return HTTPStatus.NOT_FOUND, {"error": f"{DATA_BLOB} not found"}
    if not isinstance(items, list):
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{DATA_BLOB} is not a JSON array"}

    try:
        page, page_size = parse_pagination(qs)
        filtered = filter_items(items, qs)
        sorted_items = sort_items(filtered, _first(qs, "sort") or "date-desc")
    except ValueError as e:
        return HTTPStatus.BAD_REQUEST, {"error": str(e)}

    page_items, total_pages = paginate(sorted_items, page, page_size)
    return HTTPStatus.OK, {
        "items": page_items,
        "total": len(filtered),
        "page": min(page, total_pages or 1),
        "page_size": page_size,
        "total_pages": total_pages,
    }


def api_opportunity_ids(qs: dict) -> tuple[int, dict]:
    items = fetch_parsed(DATA_BLOB)
    if items is None:
        return HTTPStatus.NOT_FOUND, {"error": f"{DATA_BLOB} not found"}
    if not isinstance(items, list):
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{DATA_BLOB} is not a JSON array"}
    try:
        filtered = filter_items(items, qs)
    except ValueError as e:
        return HTTPStatus.BAD_REQUEST, {"error": str(e)}
    ids = [(d.get("thread") or {}).get("id") for d in filtered]
    ids = [i for i in ids if i]
    return HTTPStatus.OK, {"ids": ids, "total": len(ids)}


def api_intel(qs: dict) -> tuple[int, dict]:
    """Re-aggregate intel over a filtered slice of opportunities.

    Same filter params as /api/opportunities. Powers the Market Intelligence
    tab's pivot mode: pin tool=Nessus and the returned counts describe only
    threads that mention Nessus, so the pain_point_categories list is
    effectively "all pain points of Nessus".
    """
    from profiler import aggregate_profiles

    items = fetch_parsed(DATA_BLOB)
    if items is None:
        return HTTPStatus.NOT_FOUND, {"error": f"{DATA_BLOB} not found"}
    if not isinstance(items, list):
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{DATA_BLOB} is not a JSON array"}
    try:
        filtered = filter_items(items, qs)
    except ValueError as e:
        return HTTPStatus.BAD_REQUEST, {"error": str(e)}
    return HTTPStatus.OK, aggregate_profiles(filtered)


def api_sources(_qs: dict) -> tuple[int, dict]:
    items = fetch_parsed(DATA_BLOB)
    if items is None:
        return HTTPStatus.NOT_FOUND, {"error": f"{DATA_BLOB} not found"}
    if not isinstance(items, list):
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"{DATA_BLOB} is not a JSON array"}

    counts: dict[str, dict] = {}
    for d in items:
        thread = d.get("thread") or {}
        key = _source_category_key(thread)
        entry = counts.get(key)
        if entry is None:
            counts[key] = {"key": key, "label": _source_category_label(thread), "count": 1}
        else:
            entry["count"] += 1
    sources = sorted(counts.values(), key=lambda s: s["label"].lower())
    return HTTPStatus.OK, {"sources": sources}


API_ROUTES = {
    "/api/opportunities": api_opportunities,
    "/api/opportunities/ids": api_opportunity_ids,
    "/api/sources": api_sources,
    "/api/intel": api_intel,
}


# --- HTTP handler ----------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path or "/"

        # API routes
        api_handler = API_ROUTES.get(path)
        if api_handler is not None:
            qs = parse_qs(parsed.query, keep_blank_values=False)
            try:
                status, payload = api_handler(qs)
            except Exception:  # noqa: BLE001
                log.exception("API handler %s crashed", path)
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal error"})
                return
            self._send_json(status, payload)
            return

        # Static-file serving (unchanged behaviour)
        name = path.lstrip("/") or INDEX
        if name.endswith("/"):
            name += INDEX

        result = fetch(name)
        if result is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        body, ctype = result
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=60")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=60")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002
        log.info("%s - %s", self.address_string(), format % args)


def serve(fetch_impl: FetchFn, port: int = PORT) -> None:
    """Install a fetch backend and serve the dashboard forever.

    Called by the prod entrypoint with a GCS-backed backend, and by main.py
    (in a daemon thread) with a local-files backend for `python main.py`.
    """
    global _fetch_impl
    _fetch_impl = fetch_impl
    log.info("Dashboard server starting on :%d", port)
    ThreadingHTTPServer(("", port), Handler).serve_forever()


def _make_gcs_fetch() -> FetchFn:
    """Build the GCS-backed fetch backend used by the prod Cloud Run service."""
    # Imported lazily so local-dev (main.py) doesn't pull in google-cloud-storage
    # just to import this module.
    from google.cloud import storage

    bucket_name = os.environ["DASHBOARD_BUCKET"]
    bucket = storage.Client().bucket(bucket_name)
    log.info("Backend: GCS bucket %s (cache_seconds=%d)", bucket_name, CACHE_SECONDS)

    cache: dict[str, tuple[float, bytes, str]] = {}

    def fetch_gcs(name: str) -> tuple[bytes, str] | None:
        now = time.time()
        cached = cache.get(name)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1], cached[2]
        blob = bucket.blob(name)
        if not blob.exists():
            return None
        body = blob.download_as_bytes()
        ctype = guess_type(name)[0] or "application/octet-stream"
        cache[name] = (now, body, ctype)
        return body, ctype

    return fetch_gcs


def main():
    serve(_make_gcs_fetch(), PORT)


if __name__ == "__main__":
    main()
