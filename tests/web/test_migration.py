"""End-to-end regression coverage: a pre-fix DB (item_tags empty despite tags_json data) must be
repaired purely by opening it with the current code -- no rescan required."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from atomik_meme_web.api import create_app
from atomik_meme_web.config import WebConfig
from atomik_meme_web.db import Database
from atomik_meme_web.services import resolve_data_dir_for_library
from tests.web.conftest import make_jpeg


def _seed_pre_fix_db(db_path: Path, media_path: Path) -> None:
    """Write an `items` row with `tags_json` populated but no matching `item_tags` rows, exactly
    as the reported regression found on a real `output/.meme-web/index.db`."""
    db = Database(db_path)
    st = media_path.stat()
    now = "2026-01-01T00:00:00Z"
    with db.write() as conn:
        conn.execute(
            """INSERT INTO items (
                   path, rel_path, name, stem, ext, media_type, size_bytes, mtime_ns,
                   created_at, modified_at, sha256, indexed_at, tags_json,
                   category_name, category_folder, nsfw
               ) VALUES (?, ?, ?, ?, ?, 'image', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                str(media_path),
                "04-office-software-humor/image/a.jpg",
                "a.jpg",
                "a",
                ".jpg",
                st.st_size,
                st.st_mtime_ns,
                now,
                now,
                "a" * 64,
                now,
                json.dumps(["excel", "office"]),
                "office-software-humor",
                "04-office-software-humor",
            ),
        )
    db.close()


def test_reopening_the_web_app_repairs_item_tags_without_a_rescan(tmp_path: Path, library: Path):
    media = library / "04-office-software-humor" / "image" / "a.jpg"
    make_jpeg(media)

    cfg = WebConfig(
        library=library,
        data_dir=tmp_path / "webdata",
        scan_on_start=False,  # the point of this test: no scan ever runs
        scan_interval_min=0,
        open_browser=False,
    )
    _seed_pre_fix_db(resolve_data_dir_for_library(cfg, library) / "index.db", media)

    fastapi_app = create_app(cfg, dist_dir=tmp_path / "no-dist")
    with TestClient(fastapi_app, headers={"X-Atomik-Meme": "1"}) as client:
        assert client.get("/api/scan/status").json()["last"] is None  # confirm: no scan ran

        tags = {t["tag"]: t["count"] for t in client.get("/api/tags").json()["tags"]}
        assert tags == {"excel": 1, "office": 1}

        items = client.get("/api/items", params={"tag": "excel", "nsfw": "include"}).json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "a.jpg"
