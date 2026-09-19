"""Duplicate detection edge cases: degenerate dHash exclusion, exact-group query count."""

from __future__ import annotations

from pathlib import Path

from atomik_meme_web.duplicates import _find_exact_groups
from tests.web.conftest import make_jpeg


def test_degenerate_dhash_images_are_not_grouped_as_near_duplicates(
    library: Path, client, fastapi_app
):
    """A flat/solid-colour image's dHash is all-zero bits regardless of its actual colour, so
    two unrelated flat-coloured memes must never be reported as near-duplicates of each other."""
    make_jpeg(
        library / "04-office-software-humor" / "image" / "red.jpg", color=(255, 0, 0), size=(64, 64)
    )
    make_jpeg(
        library / "04-office-software-humor" / "image" / "blue.jpg",
        color=(0, 0, 255),
        size=(64, 64),
    )
    fastapi_app.state.indexer.scan()

    items = client.get("/api/items", params={"nsfw": "include"}).json()["items"]
    assert len(items) == 2
    for item in items:
        client.get(f"/api/items/{item['id']}/thumb")

    rows = fastapi_app.state.db.conn.execute("SELECT name, dhash FROM items").fetchall()
    dhashes = {row["dhash"] for row in rows}
    assert dhashes == {"0" * 16}, "both flat images should hash to the same degenerate value"

    data = client.get("/api/duplicates").json()
    near_groups = [g for g in data["groups"] if g["kind"] == "near"]
    assert near_groups == []


def test_find_exact_groups_issues_a_constant_number_of_queries(library: Path, make_indexer):
    """3 duplicate groups (6 files) must not cost 3x the queries of 1 group -- a prior bug
    issued one extra `SELECT` per group (N+1) instead of a single grouped fetch."""
    for i in range(3):
        a = library / "04-office-software-humor" / "image" / f"group{i}-a.jpg"
        b = library / "04-office-software-humor" / "image" / f"group{i}-b.jpg"
        make_jpeg(a, color=(10 * i, 20, 30))
        b.write_bytes(a.read_bytes())
    indexer = make_indexer()
    indexer.scan()

    conn = indexer.db.conn
    queries: list[str] = []

    class _CountingConnProxy:
        """`sqlite3.Connection.execute` is read-only, so count calls via a thin wrapper instead."""

        def execute(self, sql, *args, **kwargs):
            queries.append(sql)
            return conn.execute(sql, *args, **kwargs)

    groups = _find_exact_groups(_CountingConnProxy())

    assert len(groups) == 3
    assert all(len(g["ids"]) == 2 for g in groups)
    assert len(queries) <= 2, f"expected O(1) queries regardless of group count, got {queries}"
