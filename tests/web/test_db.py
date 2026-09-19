"""Schema: new indexes exist and are actually used by the query planner (item 10), plus the
`item_tags` backfill-on-open and `rebuild_derived_tables` escape hatch (regression fix)."""

from __future__ import annotations

import json
from pathlib import Path

from atomik_meme_web.db import SCHEMA_VERSION, Database


def test_schema_version_is_recorded(tmp_path: Path):
    db = Database(tmp_path / "index.db")
    row = db.conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert int(row["value"]) == SCHEMA_VERSION


def test_new_indexes_exist(tmp_path: Path):
    db = Database(tmp_path / "index.db")
    names = {
        row["name"]
        for row in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='items'"
        ).fetchall()
    }
    assert "idx_items_size" in names
    assert "idx_items_duration" in names
    assert "idx_items_name_nocase" in names


def test_sort_by_size_uses_the_size_index(tmp_path: Path):
    db = Database(tmp_path / "index.db")
    plan = db.conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM items ORDER BY size_bytes DESC"
    ).fetchall()
    plan_text = " ".join(row["detail"] for row in plan)
    assert "idx_items_size" in plan_text


def test_item_tags_table_and_index_exist(tmp_path: Path):
    db = Database(tmp_path / "index.db")
    tables = {
        row["name"]
        for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "item_tags" in tables
    indexes = {
        row["name"]
        for row in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='item_tags'"
        ).fetchall()
    }
    assert "idx_item_tags_tag" in indexes


def test_existing_db_gains_new_indexes_on_reopen(tmp_path: Path):
    """An "existing" DB (as if created by an older `SCHEMA_VERSION`) migrates on open: opening a
    `Database` a second time against the same file must not fail, and must still have every
    current index (all `CREATE INDEX IF NOT EXISTS`, so this is idempotent by construction)."""
    path = tmp_path / "index.db"
    Database(path)
    db2 = Database(path)
    names = {
        row["name"]
        for row in db2.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='items'"
        ).fetchall()
    }
    assert "idx_items_size" in names


def _insert_raw_item(conn, *, name: str, sha256: str, tags: list[str]) -> int:
    """Insert a bare `items` row directly (bypassing the indexer/`_sync_item_tags`), simulating
    data written before `item_tags` existed: `tags_json` is populated, `item_tags` is not."""
    now = "2026-01-01T00:00:00Z"
    cur = conn.execute(
        """INSERT INTO items (
               path, rel_path, name, stem, ext, media_type, size_bytes, mtime_ns,
               created_at, modified_at, sha256, indexed_at, tags_json, nsfw
           ) VALUES (?, ?, ?, ?, ?, 'image', 100, 1, ?, ?, ?, ?, ?, 0)""",
        (
            f"/lib/{name}",
            name,
            name,
            name.rsplit(".", 1)[0],
            ".jpg",
            now,
            now,
            sha256,
            now,
            json.dumps(tags),
        ),
    )
    return cur.lastrowid


def test_backfill_populates_item_tags_from_existing_tags_json_when_empty(tmp_path: Path):
    """Regression: a DB whose `items` rows predate `item_tags` (or whose `item_tags` was
    otherwise never populated) must be repaired on open, without needing a rescan."""
    path = tmp_path / "index.db"
    db1 = Database(path)
    with db1.write() as conn:
        _insert_raw_item(
            conn, name="a.jpg", sha256="a" * 64, tags=["Excel", " Office ", "excel", ""]
        )
    db1.close()

    # Sanity check the regression precondition before reopening.
    import sqlite3

    raw = sqlite3.connect(path)
    assert raw.execute("SELECT COUNT(*) FROM item_tags").fetchone()[0] == 0
    raw.close()

    db2 = Database(path)  # the fix runs its backfill here, inside __init__
    tags = {row["tag"] for row in db2.conn.execute("SELECT tag FROM item_tags").fetchall()}
    assert tags == {"excel", "office"}, "expected lowercase, trimmed, deduped, empty-string-safe"


def test_backfill_does_not_duplicate_rows_already_synced_normally(tmp_path: Path):
    """Once `item_tags` has ANY rows (the normal, in-sync case), the backfill must not run again
    and duplicate entries for items that were already synced by the indexer."""
    path = tmp_path / "index.db"
    db = Database(path)
    with db.write() as conn:
        item_id = _insert_raw_item(conn, name="a.jpg", sha256="a" * 64, tags=["excel"])
        conn.execute("INSERT INTO item_tags (item_id, tag) VALUES (?, ?)", (item_id, "excel"))
        _insert_raw_item(conn, name="b.jpg", sha256="b" * 64, tags=["never-synced"])
    db.close()

    db2 = Database(path)
    rows = db2.conn.execute("SELECT item_id, tag FROM item_tags").fetchall()
    # Not backfilled again (item_tags already had a row), so "b.jpg"'s tag stays missing --
    # this documents the guard's scope (whole-table, not per-item) rather than asserting it's
    # wrong: a real rescan (or `index --rebuild`) is what repairs a *partially* synced table.
    assert len(rows) == 1
    assert (rows[0]["item_id"], rows[0]["tag"]) == (
        db2.conn.execute("SELECT id FROM items WHERE name='a.jpg'").fetchone()["id"],
        "excel",
    )


def test_rebuild_derived_tables_preserves_favorites(tmp_path: Path):
    path = tmp_path / "index.db"
    db = Database(path)
    sha = "a" * 64
    with db.write() as conn:
        _insert_raw_item(conn, name="a.jpg", sha256=sha, tags=["excel"])
        conn.execute(
            "INSERT INTO favorites (sha256, created_at) VALUES (?, ?)",
            (sha, "2026-01-01T00:00:00Z"),
        )
        conn.execute(
            "INSERT INTO views (sha256, viewed_at) VALUES (?, ?)", (sha, "2026-01-01T00:00:00Z")
        )

    db.rebuild_derived_tables()

    # items/items_fts/item_tags are gone (empty, ready for a rescan)...
    assert db.conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 0
    assert db.conn.execute("SELECT COUNT(*) c FROM item_tags").fetchone()["c"] == 0
    # ...but favorites/views, keyed by sha256, survive untouched.
    assert db.conn.execute("SELECT COUNT(*) c FROM favorites").fetchone()["c"] == 1
    assert db.conn.execute("SELECT sha256 FROM favorites").fetchone()["sha256"] == sha
    assert db.conn.execute("SELECT COUNT(*) c FROM views").fetchone()["c"] == 1
