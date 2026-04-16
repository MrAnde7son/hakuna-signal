"""Cloud Run Job entrypoint.

Hydrates state from GCS, runs the pipeline once, then pushes state back.
This wrapper exists so main.py stays unmodified — its in-process schedule
loop is replaced by Cloud Scheduler firing this entrypoint every 30 min.
"""
import logging
import os
import sys
from pathlib import Path

from google.cloud import storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("cloud_entrypoint")

ROOT = Path(__file__).resolve().parent.parent
DB_FILE = ROOT / "data" / "seen_threads.db"
REPORTS_DIR = ROOT / "reports"
GARTNER_DUMP_DIR = ROOT / "gartner_dump"
REDDIT_DUMP_DIR = ROOT / "reddit_dump"
REPORTS_PREFIX = "reports/"
GARTNER_PREFIX = "gartner_dump/"
REDDIT_PREFIX = "reddit_dump/"
DB_OBJECT = "seen_threads.db"


def _bucket():
    name = os.environ.get("STATE_BUCKET")
    if not name:
        raise RuntimeError("STATE_BUCKET env var is required")
    return storage.Client().bucket(name)


def hydrate(bucket) -> None:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    db_blob = bucket.blob(DB_OBJECT)
    if db_blob.exists():
        logger.info("Restoring %s from gs://%s/%s", DB_FILE.name, bucket.name, DB_OBJECT)
        db_blob.download_to_filename(DB_FILE)
    else:
        logger.info("No prior DB in bucket — starting fresh")

    REPORTS_DIR.mkdir(exist_ok=True)
    restored = 0
    for blob in bucket.list_blobs(prefix=REPORTS_PREFIX):
        rel = blob.name[len(REPORTS_PREFIX):]
        if not rel:
            continue
        dest = REPORTS_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(dest)
        restored += 1
    logger.info("Restored %d files into %s/", restored, REPORTS_DIR.name)

    # Restore pre-crawled Gartner dump (uploaded by local crawl runs)
    gartner_restored = 0
    for blob in bucket.list_blobs(prefix=GARTNER_PREFIX):
        rel = blob.name[len(GARTNER_PREFIX):]
        if not rel:
            continue
        dest = GARTNER_DUMP_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(dest)
        gartner_restored += 1
    if gartner_restored:
        logger.info("Restored %d Gartner dump files", gartner_restored)

    # Restore Reddit dump (uploaded by scripts/fetch_reddit.py from a
    # residential IP, since Reddit 403s Cloud Run egress).
    reddit_restored = 0
    for blob in bucket.list_blobs(prefix=REDDIT_PREFIX):
        rel = blob.name[len(REDDIT_PREFIX):]
        if not rel:
            continue
        dest = REDDIT_DUMP_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(dest)
        reddit_restored += 1
    if reddit_restored:
        logger.info("Restored %d Reddit dump files", reddit_restored)


def push(bucket) -> None:
    if DB_FILE.exists():
        logger.info("Uploading %s -> gs://%s/%s", DB_FILE.name, bucket.name, DB_OBJECT)
        bucket.blob(DB_OBJECT).upload_from_filename(DB_FILE)
    else:
        logger.warning("DB file missing after run — nothing to upload")

    if not REPORTS_DIR.exists():
        return

    # Always mirror reports/ into the state bucket under the reports/ prefix
    # so a hydrate-only scenario (e.g. dashboard bucket missing) still works.
    uploaded = 0
    for path in REPORTS_DIR.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        bucket.blob(rel).upload_from_filename(path)
        uploaded += 1
    logger.info("Uploaded %d files from %s/ to state bucket", uploaded, REPORTS_DIR.name)


def push_dashboard() -> None:
    """Mirror reports/ into the dashboard bucket flat (no reports/ prefix).

    The dashboard Cloud Run service expects dashboard.html and data.js at the
    bucket root. This is a no-op if DASHBOARD_BUCKET isn't configured (e.g.
    deploys without the dashboard.tf module).
    """
    name = os.environ.get("DASHBOARD_BUCKET")
    if not name:
        logger.info("DASHBOARD_BUCKET not set — skipping dashboard mirror")
        return
    if not REPORTS_DIR.exists():
        return

    bucket = storage.Client().bucket(name)
    uploaded = 0
    for path in REPORTS_DIR.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(REPORTS_DIR).as_posix()
        bucket.blob(rel).upload_from_filename(path)
        uploaded += 1
    logger.info("Uploaded %d files from %s/ to dashboard bucket %s", uploaded, REPORTS_DIR.name, name)


def main() -> int:
    bucket = _bucket()

    try:
        hydrate(bucket)
    except Exception:
        logger.exception("Hydrate failed — aborting before pipeline run to avoid clobbering state")
        return 1

    # Import after hydrate so db.py opens the restored file, not a fresh one.
    import main as pipeline  # noqa: PLC0415  - intentional late import

    try:
        pipeline.run_pipeline()
    except Exception:
        logger.exception("Pipeline run failed")
        # Still push so partial state (e.g. dedup marks) is preserved.
        try:
            push(bucket)
            push_dashboard()
        except Exception:
            logger.exception("Push after failure also failed")
        return 1

    push(bucket)
    push_dashboard()
    return 0


if __name__ == "__main__":
    sys.exit(main())
