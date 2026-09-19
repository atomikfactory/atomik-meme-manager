"""SQLite connection management and schema. WAL mode, one file per data dir.

A `Database` hands out one connection per thread (via `threading.local`) so uvicorn's
threadpool-executed sync endpoints each get their own `sqlite3.Connection` onto the same
WAL-mode file (safe for concurrent readers + one writer). Writes go through `Database.write()`,
which serialises writers with a lock and commits/rolls back automatically. Every connection ever
opened is tracked so `close_all()` (used at server shutdown) can close them regardless of which
thread opened them.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 2

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  rel_path TEXT NOT NULL,
  name TEXT NOT NULL, stem TEXT NOT NULL, ext TEXT NOT NULL,
  media_type TEXT NOT NULL CHECK (media_type IN ('image','gif','video')),
  size_bytes INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
  created_at TEXT NOT NULL, modified_at TEXT NOT NULL,
  width INTEGER, height INTEGER, duration_s REAL, fps REAL, frame_count INTEGER,
  sha256 TEXT NOT NULL, dhash TEXT,
  indexed_at TEXT NOT NULL,
  sidecar_path TEXT, sidecar_mtime_ns INTEGER,
  title TEXT, description TEXT, ocr_text TEXT,
  tags_json TEXT NOT NULL DEFAULT '[]', topics_json TEXT NOT NULL DEFAULT '[]',
  tone_json TEXT NOT NULL DEFAULT '[]',
  meme_type TEXT, template TEXT,
  category_id INTEGER, category_name TEXT, category_folder TEXT,
  nsfw INTEGER NOT NULL DEFAULT 0, confidence REAL
);
CREATE INDEX IF NOT EXISTS idx_items_sha ON items(sha256);
CREATE INDEX IF NOT EXISTS idx_items_type_created ON items(media_type, created_at);
CREATE INDEX IF NOT EXISTS idx_items_category ON items(category_name);
CREATE INDEX IF NOT EXISTS idx_items_dhash ON items(dhash);
CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at);
-- schema_version 2: sort-by-size/duration/name each did a full table scan + sort.
CREATE INDEX IF NOT EXISTS idx_items_size ON items(size_bytes);
CREATE INDEX IF NOT EXISTS idx_items_duration ON items(duration_s);
CREATE INDEX IF NOT EXISTS idx_items_name_nocase ON items(name COLLATE NOCASE);

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
  item_id UNINDEXED, name, title, description, ocr_text, tags, topics, category,
  tokenize = 'trigram'
);

CREATE TABLE IF NOT EXISTS favorites (
  sha256 TEXT PRIMARY KEY, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS views (
  id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL, viewed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_views_sha ON views(sha256, viewed_at);
CREATE TABLE IF NOT EXISTS collections (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, query TEXT NOT NULL DEFAULT '',
  filters_json TEXT NOT NULL DEFAULT '{}', icon TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scans (
  id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
  total INTEGER, added INTEGER, modified INTEGER, removed INTEGER, moved INTEGER,
  duration_s REAL, error TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- schema_version 2: normalised tags, maintained by the indexer alongside items.tags_json, so
-- the `tag` filter and /api/tags counts don't need an O(N) `json_each` scan over every row.
CREATE TABLE IF NOT EXISTS item_tags (
  item_id INTEGER NOT NULL, tag TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags(tag);
CREATE INDEX IF NOT EXISTS idx_item_tags_item ON item_tags(item_id);
"""


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _backfill_item_tags_if_empty(conn: sqlite3.Connection) -> None:
    """Repopulate `item_tags` from `items.tags_json` if it's empty but `items` has rows.

    General pattern for a derived/normalised table added by a schema change: `CREATE TABLE IF
    NOT EXISTS` alone only creates the table -- it never populates it for rows that already
    existed before the table did, and the indexer only writes `item_tags` when it actually
    (re)processes an item, which an unchanged file's `POST /api/scan` never does. So on every
    open, if the derived table is empty while its source data isn't, treat that as "never
    backfilled" and (re)populate it once, here, atomically with the schema creation. Guarding on
    "currently empty" (rather than a one-time version check) also makes this self-healing if the
    table is ever manually cleared or lost.

    Normalisation (`LOWER(TRIM(...))`) must match `indexer.normalize_tag` exactly, or a
    freshly-scanned item and a backfilled one would disagree on a tag's spelling.
    """
    has_items = conn.execute("SELECT 1 FROM items LIMIT 1").fetchone() is not None
    if not has_items:
        return
    has_item_tags = conn.execute("SELECT 1 FROM item_tags LIMIT 1").fetchone() is not None
    if has_item_tags:
        return
    conn.execute(
        """INSERT INTO item_tags (item_id, tag)
           SELECT DISTINCT items.id, LOWER(TRIM(je.value))
           FROM items, json_each(items.tags_json) je
           WHERE TRIM(je.value) <> ''"""
    )


class Database:
    """One SQLite file, one connection per thread, WAL mode."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._all_conns_lock = threading.Lock()
        self._all_conns: list[sqlite3.Connection] = []
        init_conn = _connect(self.path)
        try:
            init_conn.executescript(_SCHEMA_SQL)
            _backfill_item_tags_if_empty(init_conn)
            init_conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            init_conn.commit()
        finally:
            init_conn.close()

    @property
    def conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = _connect(self.path)
            self._local.conn = conn
            with self._all_conns_lock:
                self._all_conns.append(conn)
        return conn

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            conn = self.conn
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def close(self) -> None:
        """Close the calling thread's own connection (if it has one)."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
            with self._all_conns_lock:
                if conn in self._all_conns:
                    self._all_conns.remove(conn)

    def rebuild_derived_tables(self) -> None:
        """Drop and recreate `items`/`items_fts`/`item_tags` (favorites/views/collections/scans
        untouched). A manual escape hatch (`atomik-meme-web index --rebuild`) for when the derived
        tables have drifted out of sync with the library -- the caller is expected to run a full
        scan immediately after, which repopulates everything from disk. Favorites/views survive
        because they're keyed by sha256, not by the (now-reset) item id.
        """
        with self._write_lock:
            conn = self.conn
            conn.execute("DROP TABLE IF EXISTS items_fts")
            conn.execute("DROP TABLE IF EXISTS item_tags")
            conn.execute("DROP TABLE IF EXISTS items")
            conn.commit()
            conn.executescript(_SCHEMA_SQL)
            conn.commit()

    def close_all(self) -> None:
        """Close every connection this `Database` has ever handed out, from any thread.

        Used at server shutdown: request-handling, scan, and prewarm threads each have their own
        connection, and none of them are necessarily the thread calling this.
        """
        with self._all_conns_lock:
            conns, self._all_conns = self._all_conns, []
        for conn in conns:
            try:
                conn.close()
            except sqlite3.Error:
                pass
        if getattr(self._local, "conn", None) in conns:
            self._local.conn = None
