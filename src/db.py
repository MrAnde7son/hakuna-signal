import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "seen_threads.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# Single long-lived connection. The pipeline is single-threaded, so re-opening
# per call (and re-running CREATE TABLE / migration checks each time) was
# thousands of wasted statements per run.
_conn: sqlite3.Connection | None = None


def get_connection() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    _conn = sqlite3.connect(DB_PATH, isolation_level=None)
    _init_schema(_conn)
    return _conn


def _init_schema(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_threads (
            thread_id TEXT PRIMARY KEY,
            source TEXT DEFAULT 'reddit',
            category TEXT,
            title TEXT,
            score INTEGER,
            action TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            viewed INTEGER DEFAULT 0,
            opportunity_data TEXT
        )
    """)
    cursor = conn.execute("PRAGMA table_info(seen_threads)")
    columns = [row[1] for row in cursor.fetchall()]
    if "viewed" not in columns:
        conn.execute("ALTER TABLE seen_threads ADD COLUMN viewed INTEGER DEFAULT 0")
    if "opportunity_data" not in columns:
        conn.execute("ALTER TABLE seen_threads ADD COLUMN opportunity_data TEXT")
    if "subreddit" in columns and "category" not in columns:
        conn.execute("ALTER TABLE seen_threads RENAME COLUMN subreddit TO category")
    if "source" not in columns:
        conn.execute("ALTER TABLE seen_threads ADD COLUMN source TEXT DEFAULT 'reddit'")
        conn.execute("UPDATE seen_threads SET source = 'reddit' WHERE source IS NULL")

    # Migrate single-column PK -> composite (source, thread_id) PK so that
    # bare numeric Spiceworks IDs can't collide with Reddit base36 IDs.
    pk_cols = [row[1] for row in conn.execute("PRAGMA table_info(seen_threads)") if row[5] > 0]
    if pk_cols == ["thread_id"]:
        conn.execute("BEGIN")
        conn.execute("""
            CREATE TABLE seen_threads_new (
                source TEXT NOT NULL DEFAULT 'reddit',
                thread_id TEXT NOT NULL,
                category TEXT,
                title TEXT,
                score INTEGER,
                action TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                viewed INTEGER DEFAULT 0,
                opportunity_data TEXT,
                PRIMARY KEY (source, thread_id)
            )
        """)
        conn.execute("""
            INSERT INTO seen_threads_new
              (source, thread_id, category, title, score, action, created_at, viewed, opportunity_data)
            SELECT
              COALESCE(source, 'reddit'), thread_id, category, title, score, action, created_at, viewed, opportunity_data
            FROM seen_threads
        """)
        conn.execute("DROP TABLE seen_threads")
        conn.execute("ALTER TABLE seen_threads_new RENAME TO seen_threads")
        conn.execute("COMMIT")


def is_seen(source: str, thread_id: str) -> bool:
    row = get_connection().execute(
        "SELECT 1 FROM seen_threads WHERE source = ? AND thread_id = ?",
        (source, thread_id),
    ).fetchone()
    return row is not None


def mark_seen(thread_id: str, source: str, category: str, title: str, score: int, action: str,
              opportunity_data: dict | None = None):
    data_json = json.dumps(opportunity_data) if opportunity_data else None
    get_connection().execute(
        "INSERT OR IGNORE INTO seen_threads (thread_id, source, category, title, score, action, opportunity_data) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (thread_id, source, category, title, score, action, data_json),
    )


def get_all_scored_opportunities() -> list[dict]:
    """Return all scored threads from history for market intelligence aggregation."""
    rows = get_connection().execute(
        "SELECT opportunity_data FROM seen_threads WHERE opportunity_data IS NOT NULL"
    ).fetchall()
    return [json.loads(r[0]) for r in rows]


def get_unviewed_opportunities() -> list[dict]:
    """Return alerted threads that haven't been viewed in a report yet."""
    rows = get_connection().execute(
        "SELECT opportunity_data FROM seen_threads WHERE action = 'alerted' AND viewed = 0 AND opportunity_data IS NOT NULL"
    ).fetchall()
    return [json.loads(r[0]) for r in rows]


def mark_viewed(keys: list[tuple[str, str]]):
    """Mark (source, thread_id) pairs as viewed after they appear in a report."""
    if not keys:
        return
    get_connection().executemany(
        "UPDATE seen_threads SET viewed = 1 WHERE source = ? AND thread_id = ?",
        keys,
    )
