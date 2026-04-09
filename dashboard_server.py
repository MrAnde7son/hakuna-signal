"""Cloud Run service entrypoint: serves the static dashboard from GCS.

Uses the same container image as the pipeline job. The Cloud Run service
overrides the image CMD to run this file instead of cloud_entrypoint.py.

Files come from $DASHBOARD_BUCKET. We cache GCS reads in-memory for
$CACHE_SECONDS (default 60s) so a single page load doesn't fan out N GCS
GETs for dashboard.html + data.js + intel.json.
"""
import logging
import os
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from mimetypes import guess_type
from urllib.parse import urlparse

from google.cloud import storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("dashboard")

BUCKET_NAME = os.environ["DASHBOARD_BUCKET"]
PORT = int(os.environ.get("PORT", "8080"))
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "60"))
INDEX = "dashboard.html"

_bucket = storage.Client().bucket(BUCKET_NAME)
_cache: dict[str, tuple[float, bytes, str]] = {}


def fetch(name: str) -> tuple[bytes, str] | None:
    now = time.time()
    cached = _cache.get(name)
    if cached and now - cached[0] < CACHE_SECONDS:
        return cached[1], cached[2]

    blob = _bucket.blob(name)
    if not blob.exists():
        return None
    body = blob.download_as_bytes()
    ctype = guess_type(name)[0] or "application/octet-stream"
    _cache[name] = (now, body, ctype)
    return body, ctype


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path.lstrip("/") or INDEX
        if path.endswith("/"):
            path += INDEX

        result = fetch(path)
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

    def log_message(self, format, *args):  # noqa: A002
        log.info("%s - %s", self.address_string(), format % args)


def main():
    log.info("Dashboard server starting on :%d (bucket=%s)", PORT, BUCKET_NAME)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
