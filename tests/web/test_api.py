"""Misc API endpoints: health, stats, config, categories, tags, suggest, recent, random,
favorites/views, collections CRUD, scan status/trigger, duplicates, OpenAPI docs.
"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

from tests.web.conftest import make_jpeg, make_sidecar


def _seed_two_items(library: Path) -> None:
    a = library / "04-office-software-humor" / "image" / "a.jpg"
    make_jpeg(a, color=(10, 10, 200))
    make_sidecar(a, title="Alpha Meme", tags=["alpha"], category_name="office-software-humor")

    b = library / "00 - NSFW" / "image" / "b.jpg"
    make_jpeg(b, color=(200, 10, 10))
    make_sidecar(b, title="Beta Meme", tags=["beta"], nsfw=True, category_name="nsfw")


def test_health(client, fastapi_app, library):
    _seed_two_items(library)
    fastapi_app.state.indexer.scan()
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["items"] == 2
    assert data["library_root"] == str(library)
    assert isinstance(data["ffmpeg"], bool)
    assert data["ffmpeg"] == (shutil.which("ffmpeg") is not None)


def test_config_endpoint(client, web_config):
    resp = client.get("/api/config")
    data = resp.json()
    assert data["inbox_enabled"] is True
    assert data["nsfw_default"] == "hide"
    assert data["thumb_sizes"] == [320, 640]
    assert data["library_root"] == str(web_config.library)


def test_stats(client, fastapi_app, library):
    _seed_two_items(library)
    fastapi_app.state.indexer.scan()
    resp = client.get("/api/stats")
    data = resp.json()
    assert data["total"] == 2
    assert data["by_type"]["image"] == 2
    assert data["nsfw"] == 1
    assert data["total_bytes"] > 0
    assert data["scanning"] is False
    assert data["last_scan"]["added"] == 2
    names = {c["name"] for c in data["categories"]}
    assert {"office-software-humor", "nsfw"} <= names


def test_nsfw_category_membership_is_nsfw_regardless_of_sidecar_flag(client, fastapi_app, library):
    """An item hinted into 00 - NSFW/ with `analysis.nsfw: false` must still be treated as NSFW
    everywhere the web app cares: the index flag, the default `nsfw=exclude` filter, the
    `nsfw=only` filter, and the /api/stats NSFW count."""
    hinted = library / "00 - NSFW" / "image" / "hinted.jpg"
    make_jpeg(hinted, color=(5, 5, 5))
    make_sidecar(hinted, title="Hinted Not Flagged", nsfw=False, category_name="nsfw")
    fastapi_app.state.indexer.scan()

    default_resp = client.get("/api/items").json()
    assert all(item["name"] != "hinted.jpg" for item in default_resp["items"])

    only_resp = client.get("/api/items", params={"nsfw": "only"}).json()
    names = {item["name"] for item in only_resp["items"]}
    assert "hinted.jpg" in names
    hinted_item = next(i for i in only_resp["items"] if i["name"] == "hinted.jpg")
    assert hinted_item["nsfw"] is True

    stats = client.get("/api/stats").json()
    assert stats["nsfw"] == 1


def test_categories_endpoint_derived_from_index(client, fastapi_app, library):
    _seed_two_items(library)
    fastapi_app.state.indexer.scan()
    resp = client.get("/api/categories")
    data = resp.json()
    names = {c["name"] for c in data["categories"]}
    assert {"office-software-humor", "nsfw"} <= names
    for cat in data["categories"]:
        assert cat["count"] >= 1


def test_tags_endpoint(client, fastapi_app, library):
    _seed_two_items(library)
    fastapi_app.state.indexer.scan()
    resp = client.get("/api/tags")
    tags = {t["tag"] for t in resp.json()["tags"]}
    assert {"alpha", "beta"} <= tags

    resp2 = client.get("/api/tags", params={"q": "alp"})
    tags2 = {t["tag"] for t in resp2.json()["tags"]}
    assert tags2 == {"alpha"}


def test_tags_endpoint_counts_are_correct_after_a_rescan_that_changes_tags(
    client, fastapi_app, library
):
    """`/api/tags` reads the normalised `item_tags` table; confirm its counts track
    a sidecar edit + rescan, not a stale/cached view."""
    media = library / "04-office-software-humor" / "image" / "a.jpg"
    make_jpeg(media)
    make_sidecar(media, tags=["shared-tag", "only-on-alpha"])
    other = library / "04-office-software-humor" / "image" / "b.jpg"
    make_jpeg(other, color=(9, 9, 9))
    make_sidecar(other, tags=["shared-tag"])
    fastapi_app.state.indexer.scan()

    before = {t["tag"]: t["count"] for t in client.get("/api/tags").json()["tags"]}
    assert before["shared-tag"] == 2
    assert before["only-on-alpha"] == 1

    time.sleep(0.05)
    make_sidecar(media, tags=["shared-tag", "renamed-tag"])
    fastapi_app.state.indexer.scan()

    after = {t["tag"]: t["count"] for t in client.get("/api/tags").json()["tags"]}
    assert after["shared-tag"] == 2
    assert after["renamed-tag"] == 1
    assert "only-on-alpha" not in after


def test_tag_filter_matches_the_same_items_as_before_the_item_tags_migration(
    client, fastapi_app, library
):
    media = library / "04-office-software-humor" / "image" / "a.jpg"
    make_jpeg(media)
    make_sidecar(media, tags=["excel", "office"])
    other = library / "04-office-software-humor" / "image" / "b.jpg"
    make_jpeg(other, color=(9, 9, 9))
    make_sidecar(other, tags=["unrelated"])
    fastapi_app.state.indexer.scan()

    resp = client.get("/api/items", params={"tag": "excel"})
    names = {i["name"] for i in resp.json()["items"]}
    assert names == {"a.jpg"}


def test_suggest_endpoint(client, fastapi_app, library):
    _seed_two_items(library)
    fastapi_app.state.indexer.scan()
    resp = client.get("/api/suggest", params={"q": "Alph"})
    kinds = {s["kind"] for s in resp.json()["suggestions"]}
    assert "title" in kinds or "tag" in kinds


def test_tags_and_suggest_escape_like_wildcards(client, fastapi_app, library):
    """A literal `_`/`%` typed into the search box must not act as a SQL wildcard and match
    every tag/title/name -- only things that literally contain that character."""
    _seed_two_items(library)
    fastapi_app.state.indexer.scan()

    tags_underscore = client.get("/api/tags", params={"q": "_"}).json()["tags"]
    assert tags_underscore == []
    tags_percent = client.get("/api/tags", params={"q": "%"}).json()["tags"]
    assert tags_percent == []

    suggest_underscore = client.get("/api/suggest", params={"q": "_"}).json()["suggestions"]
    assert suggest_underscore == []
    suggest_percent = client.get("/api/suggest", params={"q": "%"}).json()["suggestions"]
    assert suggest_percent == []


def test_recent_added_and_viewed(client, fastapi_app, library):
    _seed_two_items(library)
    fastapi_app.state.indexer.scan()
    resp = client.get("/api/recent", params={"kind": "added", "limit": 10})
    assert len(resp.json()["items"]) == 2

    item_id = resp.json()["items"][0]["id"]
    client.post(f"/api/items/{item_id}/view")
    viewed = client.get("/api/recent", params={"kind": "viewed"}).json()
    assert any(i["id"] == item_id for i in viewed["items"])


def test_random_endpoint(client, fastapi_app, library):
    _seed_two_items(library)
    fastapi_app.state.indexer.scan()
    resp = client.get("/api/random", params={"nsfw": "include"})
    assert resp.status_code == 200
    assert "id" in resp.json()


def test_random_404_when_no_match(client, fastapi_app, library):
    _seed_two_items(library)
    fastapi_app.state.indexer.scan()
    resp = client.get("/api/random", params={"category": "does-not-exist"})
    assert resp.status_code == 404


def test_random_accepts_the_same_filters_as_items(client, fastapi_app, library):
    """`/api/random` must accept `q`/`min_duration`/`max_duration`/`since`, not just the csv
    filters -- it shares `_build_search_params` with `/api/items` for exactly this reason."""
    _seed_two_items(library)
    fastapi_app.state.indexer.scan()

    resp_q = client.get("/api/random", params={"q": "Alpha", "nsfw": "include"})
    assert resp_q.status_code == 200
    assert resp_q.json()["name"] == "a.jpg"

    far_future = "2099-01-01T00:00:00Z"
    resp_since = client.get("/api/random", params={"since": far_future, "nsfw": "include"})
    assert resp_since.status_code == 404

    resp_duration = client.get("/api/random", params={"min_duration": 999999, "nsfw": "include"})
    assert resp_duration.status_code == 404


def test_nsfw_param_rejects_unknown_values(client):
    resp = client.get("/api/items", params={"nsfw": "not-a-real-mode"})
    assert resp.status_code == 422

    resp_random = client.get("/api/random", params={"nsfw": "not-a-real-mode"})
    assert resp_random.status_code == 422


def test_view_and_favorite_roundtrip(client, fastapi_app, library):
    _seed_two_items(library)
    fastapi_app.state.indexer.scan()
    item_id = client.get("/api/items", params={"nsfw": "include"}).json()["items"][0]["id"]

    view_resp = client.post(f"/api/items/{item_id}/view")
    assert view_resp.json()["view_count"] == 1
    view_resp2 = client.post(f"/api/items/{item_id}/view")
    assert view_resp2.json()["view_count"] == 2

    fav_resp = client.put(f"/api/items/{item_id}/favorite", json={"favorite": True})
    assert fav_resp.json()["favorite"] is True
    item = client.get(f"/api/items/{item_id}").json()
    assert item["favorite"] is True
    assert item["view_count"] == 2

    unfav_resp = client.put(f"/api/items/{item_id}/favorite", json={"favorite": False})
    assert unfav_resp.json()["favorite"] is False


def test_item_detail_includes_sidecar_and_duplicates(client, fastapi_app, library):
    a = library / "04-office-software-humor" / "image" / "dup1.jpg"
    make_jpeg(a, color=(1, 2, 3))
    make_sidecar(a, title="Dup One")
    b = library / "04-office-software-humor" / "image" / "dup2.jpg"
    b.write_bytes(a.read_bytes())  # byte-identical duplicate
    fastapi_app.state.indexer.scan()

    items = client.get("/api/items", params={"nsfw": "include"}).json()["items"]
    dup1 = next(i for i in items if i["name"] == "dup1.jpg")
    detail = client.get(f"/api/items/{dup1['id']}").json()
    assert detail["sidecar"] is not None
    assert detail["sidecar"]["analysis"]["title"] == "Dup One"
    assert len(detail["duplicates"]) == 1
    assert detail["duplicates"][0]["name"] == "dup2.jpg"


def test_item_404(client):
    assert client.get("/api/items/12345").status_code == 404


def test_collections_crud_and_builtins(client, fastapi_app, library):
    _seed_two_items(library)
    fastapi_app.state.indexer.scan()

    listing = client.get("/api/collections").json()
    builtin_ids = {c["id"] for c in listing["collections"] if c["builtin"]}
    assert "builtin:favorites" in builtin_ids
    assert "builtin:nsfw" in builtin_ids

    created = client.post(
        "/api/collections",
        json={"name": "My Faves", "query": "excel", "filters": {"favorite": True}},
    )
    assert created.status_code == 201
    coll = created.json()
    assert coll["builtin"] is False

    updated = client.put(
        f"/api/collections/{coll['id']}",
        json={"name": "My Faves 2", "query": "", "filters": {}},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "My Faves 2"

    deleted = client.delete(f"/api/collections/{coll['id']}")
    assert deleted.status_code == 204

    missing = client.delete(f"/api/collections/{coll['id']}")
    assert missing.status_code == 404

    builtin_delete = client.delete("/api/collections/builtin:favorites")
    assert builtin_delete.status_code == 400


def test_scan_status_and_trigger(client, fastapi_app, library):
    _seed_two_items(library)
    status = client.get("/api/scan/status").json()
    assert status["running"] is False
    assert status["last"] is None

    resp = client.post("/api/scan")
    assert resp.status_code == 202
    assert resp.json() == {"started": True}

    # wait for the background scan to finish
    for _ in range(100):
        status = client.get("/api/scan/status").json()
        if not status["running"]:
            break
        time.sleep(0.05)
    assert status["running"] is False
    assert status["last"]["added"] == 2


def test_scan_returns_409_when_already_running(client, fastapi_app, library, monkeypatch):
    _seed_two_items(library)
    started = threading.Event()
    release = threading.Event()

    original_scan_body = fastapi_app.state.indexer._scan_body

    def _slow_scan_body(started_at):
        started.set()
        release.wait(timeout=5)
        return original_scan_body(started_at)

    monkeypatch.setattr(fastapi_app.state.indexer, "_scan_body", _slow_scan_body)
    resp1 = client.post("/api/scan")
    assert resp1.status_code == 202
    started.wait(timeout=5)

    resp2 = client.post("/api/scan")
    assert resp2.status_code == 409
    assert resp2.json() == {"running": True}
    release.set()

    for _ in range(100):
        if not client.get("/api/scan/status").json()["running"]:
            break
        time.sleep(0.05)


def test_duplicates_endpoint(client, fastapi_app, library):
    a = library / "04-office-software-humor" / "image" / "dup1.jpg"
    make_jpeg(a, color=(1, 2, 3))
    b = library / "04-office-software-humor" / "image" / "dup2.jpg"
    b.write_bytes(a.read_bytes())
    fastapi_app.state.indexer.scan()

    resp = client.get("/api/duplicates")
    data = resp.json()
    exact_groups = [g for g in data["groups"] if g["kind"] == "exact"]
    assert len(exact_groups) == 1
    assert len(exact_groups[0]["items"]) == 2


def test_openapi_docs_available(client):
    resp = client.get("/docs")
    assert resp.status_code == 200
    schema_resp = client.get("/openapi.json")
    assert schema_resp.status_code == 200
    assert "/api/items" in schema_resp.json()["paths"]
