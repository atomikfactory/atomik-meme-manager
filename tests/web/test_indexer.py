"""Indexer: change detection, provider pipeline, move detection, and library read-only safety."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from tests.web.conftest import make_jpeg, make_sidecar


def _listing_with_mtimes(root: Path) -> dict[str, float]:
    out = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            p = Path(dirpath) / name
            out[str(p)] = p.stat().st_mtime_ns
    return out


def test_scan_adds_items_and_is_idempotent(library: Path, make_indexer):
    make_jpeg(library / "04-office-software-humor" / "image" / "excel.jpg")
    make_jpeg(library / "04-office-software-humor" / "image" / "powerpoint.jpg")

    before = _listing_with_mtimes(library)
    indexer = make_indexer()
    record = indexer.scan()
    after = _listing_with_mtimes(library)

    assert record.added == 2
    assert record.modified == 0
    assert record.removed == 0
    assert record.moved == 0
    assert before == after, "scanning must never modify library files"

    rows = indexer.db.conn.execute("SELECT * FROM items").fetchall()
    assert len(rows) == 2
    row = indexer.db.conn.execute("SELECT * FROM items WHERE name='excel.jpg'").fetchone()
    assert row["media_type"] == "image"
    assert row["category_name"] == "office-software-humor"
    assert row["category_folder"] == "04-office-software-humor"
    assert row["rel_path"] == "04-office-software-humor/image/excel.jpg"
    assert "/" in row["rel_path"] and "\\" not in row["rel_path"]

    # second scan: nothing changed
    record2 = indexer.scan()
    assert (record2.added, record2.modified, record2.removed, record2.moved) == (0, 0, 0, 0)


def test_nsfw_folder_maps_to_nsfw_category(library: Path, make_indexer):
    make_jpeg(library / "00 - NSFW" / "image" / "spicy.jpg")
    indexer = make_indexer()
    indexer.scan()
    row = indexer.db.conn.execute("SELECT * FROM items WHERE name='spicy.jpg'").fetchone()
    assert row["category_name"] == "nsfw"
    assert row["category_folder"] == "00 - NSFW"
    assert row["nsfw"] == 1  # NSFW-category membership alone is enough, even with no sidecar


def test_nsfw_category_forces_nsfw_flag_even_if_sidecar_says_false(library: Path, make_indexer):
    """A meme hinted into 00 - NSFW/ (e.g. via --category) must count as NSFW for the web app,
    regardless of what the vision model's own `analysis.nsfw` said."""
    media = library / "00 - NSFW" / "image" / "hinted-not-actually-flagged.jpg"
    make_jpeg(media)
    make_sidecar(media, nsfw=False, category_name="nsfw", category_id=0)

    indexer = make_indexer()
    indexer.scan()
    row = indexer.db.conn.execute(
        "SELECT * FROM items WHERE name='hinted-not-actually-flagged.jpg'"
    ).fetchone()
    assert row["category_name"] == "nsfw"
    assert row["nsfw"] == 1


def test_nsfw_rule_in_categories_json_forces_nsfw_flag(library: Path, make_indexer):
    """A pinned category recorded as `rule: "nsfw"` in categories.json counts as NSFW even when
    its resolved name isn't literally "nsfw" (a user could rename the pinned category)."""
    media = library / "00 - NSFW" / "image" / "renamed-pinned-category.jpg"
    make_jpeg(media)
    make_sidecar(media, nsfw=False, category_name="adult-content", category_id=0)
    (library / "categories.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "generated_at": "2026-09-17T00:00:00Z",
                "model": "",
                "profile": "fast",
                "run_id": "test00000002",
                "collection_size": 1,
                "layout_version": 2,
                "categories": [
                    {
                        "id": 0,
                        "name": "adult-content",
                        "folder": "00 - NSFW",
                        "description": "",
                        "keywords": [],
                        "count": 1,
                        "pinned": True,
                        "rule": "nsfw",
                        "source": "rule",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    indexer = make_indexer()
    indexer.scan()
    row = indexer.db.conn.execute(
        "SELECT * FROM items WHERE name='renamed-pinned-category.jpg'"
    ).fetchone()
    assert row["category_name"] == "adult-content"
    assert row["nsfw"] == 1


def test_sidecar_metadata_is_applied(library: Path, make_indexer):
    media = library / "04-office-software-humor" / "image" / "excel.jpg"
    make_jpeg(media)
    make_sidecar(media, title="Excel vs PowerPoint", tags=["excel", "powerpoint"], nsfw=False)

    indexer = make_indexer()
    indexer.scan()
    row = indexer.db.conn.execute("SELECT * FROM items WHERE name='excel.jpg'").fetchone()
    assert row["title"] == "Excel vs PowerPoint"
    assert json.loads(row["tags_json"]) == ["excel", "powerpoint"]
    assert row["ocr_text"] == "HELLO WORLD"
    assert row["nsfw"] == 0


def test_sidecar_category_overrides_pathtags_but_folder_still_recorded(library: Path, make_indexer):
    media = library / "04-office-software-humor" / "image" / "excel.jpg"
    make_jpeg(media)
    make_sidecar(media, category_name="custom-office-name", category_id=99)

    indexer = make_indexer()
    indexer.scan()
    row = indexer.db.conn.execute("SELECT * FROM items WHERE name='excel.jpg'").fetchone()
    assert row["category_name"] == "custom-office-name"
    assert row["category_id"] == 99
    assert row["category_folder"] == "04-office-software-humor"


def test_modified_file_is_reprocessed(library: Path, make_indexer):
    media = library / "04-office-software-humor" / "image" / "excel.jpg"
    make_jpeg(media, color=(10, 10, 10))
    indexer = make_indexer()
    indexer.scan()

    time.sleep(0.05)
    make_jpeg(media, color=(200, 200, 200), size=(50, 50))
    record = indexer.scan()
    assert record.modified == 1
    assert record.added == 0
    row = indexer.db.conn.execute("SELECT * FROM items WHERE name='excel.jpg'").fetchone()
    assert row["width"] == 50


def test_removed_file_is_deleted_from_index(library: Path, make_indexer):
    media = library / "04-office-software-humor" / "image" / "excel.jpg"
    make_jpeg(media)
    indexer = make_indexer()
    indexer.scan()

    media.unlink()
    record = indexer.scan()
    assert record.removed == 1
    rows = indexer.db.conn.execute("SELECT * FROM items").fetchall()
    assert len(rows) == 0


def test_deleting_two_of_three_identical_files_removes_exactly_those_two(
    library: Path, make_indexer
):
    """A prior bug (`removed_by_sha.setdefault(sha, row)`) kept only ONE removed row per
    sha256, leaving ghost rows behind when 2+ byte-identical files were deleted in one scan."""
    a = library / "04-office-software-humor" / "image" / "identical-a.jpg"
    make_jpeg(a, color=(7, 7, 7))
    b = library / "04-office-software-humor" / "image" / "identical-b.jpg"
    b.write_bytes(a.read_bytes())
    c = library / "04-office-software-humor" / "image" / "identical-c.jpg"
    c.write_bytes(a.read_bytes())

    indexer = make_indexer()
    record1 = indexer.scan()
    assert record1.added == 3

    a.unlink()
    b.unlink()
    record2 = indexer.scan()
    assert record2.removed == 2
    assert record2.added == 0
    assert record2.moved == 0

    rows = indexer.db.conn.execute("SELECT name FROM items").fetchall()
    assert {r["name"] for r in rows} == {"identical-c.jpg"}


def test_renamed_file_is_a_move_and_favorite_survives(library: Path, make_indexer):
    media = library / "04-office-software-humor" / "image" / "excel.jpg"
    make_jpeg(media)
    indexer = make_indexer()
    indexer.scan()

    row = indexer.db.conn.execute("SELECT * FROM items").fetchone()
    sha = row["sha256"]
    with indexer.db.write() as conn:
        conn.execute(
            "INSERT INTO favorites (sha256, created_at) VALUES (?, ?)",
            (sha, "2026-01-01T00:00:00Z"),
        )

    new_media = library / "04-office-software-humor" / "image" / "excel-renamed.jpg"
    media.rename(new_media)
    record = indexer.scan()
    assert record.moved == 1
    assert record.added == 0
    assert record.removed == 0

    rows = indexer.db.conn.execute("SELECT * FROM items").fetchall()
    assert len(rows) == 1
    assert rows[0]["name"] == "excel-renamed.jpg"
    assert rows[0]["sha256"] == sha

    fav = indexer.db.conn.execute("SELECT * FROM favorites WHERE sha256=?", (sha,)).fetchone()
    assert fav is not None, "favorites are keyed by sha256 and must survive a rename"


def test_sidecar_edit_refreshes_metadata_without_touching_content(library: Path, make_indexer):
    media = library / "04-office-software-humor" / "image" / "excel.jpg"
    make_jpeg(media)
    make_sidecar(media, title="Original Title")
    indexer = make_indexer()
    record1 = indexer.scan()
    assert record1.added == 1

    time.sleep(0.05)
    make_sidecar(media, title="Updated Title")
    record2 = indexer.scan()
    assert record2.added == 0
    assert record2.modified == 1

    row = indexer.db.conn.execute("SELECT * FROM items WHERE name='excel.jpg'").fetchone()
    assert row["title"] == "Updated Title"


def test_item_tags_table_is_maintained_alongside_tags_json(library: Path, make_indexer):
    media = library / "04-office-software-humor" / "image" / "excel.jpg"
    make_jpeg(media)
    make_sidecar(media, tags=["excel", "office"])
    indexer = make_indexer()
    indexer.scan()

    row = indexer.db.conn.execute("SELECT id FROM items WHERE name='excel.jpg'").fetchone()
    tags = {
        r["tag"]
        for r in indexer.db.conn.execute(
            "SELECT tag FROM item_tags WHERE item_id=?", (row["id"],)
        ).fetchall()
    }
    assert tags == {"excel", "office"}


def test_item_tags_table_is_updated_when_sidecar_tags_change_on_rescan(library: Path, make_indexer):
    media = library / "04-office-software-humor" / "image" / "excel.jpg"
    make_jpeg(media)
    make_sidecar(media, tags=["old-tag"])
    indexer = make_indexer()
    indexer.scan()

    time.sleep(0.05)
    make_sidecar(media, tags=["new-tag-one", "new-tag-two"])
    record = indexer.scan()
    assert record.modified == 1

    row = indexer.db.conn.execute("SELECT id FROM items WHERE name='excel.jpg'").fetchone()
    tags = {
        r["tag"]
        for r in indexer.db.conn.execute(
            "SELECT tag FROM item_tags WHERE item_id=?", (row["id"],)
        ).fetchall()
    }
    assert tags == {"new-tag-one", "new-tag-two"}
    assert "old-tag" not in tags


def test_item_tags_removed_when_item_is_removed(library: Path, make_indexer):
    media = library / "04-office-software-humor" / "image" / "excel.jpg"
    make_jpeg(media)
    make_sidecar(media, tags=["gone-soon"])
    indexer = make_indexer()
    indexer.scan()
    row = indexer.db.conn.execute("SELECT id FROM items WHERE name='excel.jpg'").fetchone()
    item_id = row["id"]

    media.unlink()
    (library / "04-office-software-humor" / "image" / "excel.json").unlink()
    indexer.scan()

    remaining = indexer.db.conn.execute(
        "SELECT * FROM item_tags WHERE item_id=?", (item_id,)
    ).fetchall()
    assert remaining == []


def test_scan_never_modifies_library_files(library: Path, make_indexer):
    media = library / "04-office-software-humor" / "image" / "excel.jpg"
    make_jpeg(media)
    make_sidecar(media)
    before = _listing_with_mtimes(library)
    indexer = make_indexer()
    indexer.scan()
    indexer.scan()
    after = _listing_with_mtimes(library)
    assert before == after
