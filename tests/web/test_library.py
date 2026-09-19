"""Library switching: POST /api/library, GET /api/library,
DELETE /api/library/recent, no-library startup, CSRF on the new routes."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atomik_meme_web.api import create_app
from atomik_meme_web.config import WebConfig
from tests.web.conftest import make_jpeg


def _make_library(root: Path, name: str) -> Path:
    lib = root / name
    (lib / "04-office-software-humor" / "image").mkdir(parents=True)
    return lib


def test_switch_changes_stats_and_health(tmp_path: Path, client, fastapi_app, library: Path):
    lib_b = _make_library(tmp_path, "library_b")
    make_jpeg(library / "04-office-software-humor" / "image" / "a.jpg")
    make_jpeg(lib_b / "04-office-software-humor" / "image" / "b1.jpg")
    make_jpeg(lib_b / "04-office-software-humor" / "image" / "b2.jpg")
    fastapi_app.state.services.indexer.scan()

    stats_a = client.get("/api/stats").json()
    assert stats_a["total"] == 1
    health_a = client.get("/api/health").json()
    assert health_a["library_root"] == str(library)

    resp = client.post("/api/library", json={"path": str(lib_b)})
    assert resp.status_code == 200
    data = resp.json()
    assert data["library_root"] == str(lib_b)
    assert data["scan_started"] is True

    # the switch's own background scan may still be running; poll briefly
    for _ in range(100):
        if not fastapi_app.state.services.indexer.running:
            break
        time.sleep(0.02)

    health_b = client.get("/api/health").json()
    assert health_b["library_root"] == str(lib_b)
    stats_b = client.get("/api/stats").json()
    assert stats_b["total"] == 2


def test_favorites_are_isolated_per_library_and_reappear_on_switch_back(
    tmp_path: Path, client, fastapi_app, library: Path
):
    lib_b = _make_library(tmp_path, "library_b")
    make_jpeg(library / "04-office-software-humor" / "image" / "a.jpg")
    make_jpeg(lib_b / "04-office-software-humor" / "image" / "b.jpg")
    fastapi_app.state.services.indexer.scan()

    item_a = client.get("/api/items", params={"nsfw": "include"}).json()["items"][0]
    client.put(f"/api/items/{item_a['id']}/favorite", json={"favorite": True})
    assert client.get(f"/api/items/{item_a['id']}").json()["favorite"] is True

    resp = client.post("/api/library", json={"path": str(lib_b)})
    assert resp.status_code == 200
    for _ in range(100):
        if not fastapi_app.state.services.indexer.running:
            break
        time.sleep(0.02)

    items_b = client.get("/api/items", params={"nsfw": "include"}).json()["items"]
    assert len(items_b) == 1
    assert items_b[0]["favorite"] is False

    resp_back = client.post("/api/library", json={"path": str(library)})
    assert resp_back.status_code == 200
    for _ in range(100):
        if not fastapi_app.state.services.indexer.running:
            break
        time.sleep(0.02)

    item_a_again = client.get("/api/items", params={"nsfw": "include"}).json()["items"][0]
    assert item_a_again["id"] == item_a["id"]
    assert item_a_again["favorite"] is True


def test_switch_during_a_running_scan_cancels_it(
    tmp_path: Path, client, fastapi_app, library: Path, monkeypatch
):
    lib_b = _make_library(tmp_path, "library_b")
    make_jpeg(lib_b / "04-office-software-humor" / "image" / "b.jpg")

    indexer = fastapi_app.state.services.indexer
    started = threading.Event()
    release = threading.Event()
    original_scan_body = indexer._scan_body

    def slow_scan_body(started_at):
        started.set()
        while not release.is_set():
            if indexer.cancel_requested:
                release.set()
                break
            time.sleep(0.01)
        return original_scan_body(started_at)

    monkeypatch.setattr(indexer, "_scan_body", slow_scan_body)
    indexer.try_start_background()
    assert started.wait(timeout=5)
    assert indexer.running is True

    resp = client.post("/api/library", json={"path": str(lib_b)})
    assert resp.status_code == 200
    assert indexer.cancel_requested is True
    # the OLD indexer must have stopped (been cancelled) well within the 10s budget
    for _ in range(200):
        if not indexer.running:
            break
        time.sleep(0.02)
    assert indexer.running is False


def test_switch_to_nonexistent_path_is_404(client):
    resp = client.post("/api/library", json={"path": "D:\\this\\does\\not\\exist\\at\\all"})
    assert resp.status_code == 404
    assert "detail" in resp.json()


def test_switch_to_relative_path_is_400(client):
    resp = client.post("/api/library", json={"path": "relative/path"})
    assert resp.status_code == 400


def test_switch_to_a_file_not_a_directory_is_404(tmp_path: Path, client):
    a_file = tmp_path / "not_a_dir.txt"
    a_file.write_text("hi", encoding="utf-8")
    resp = client.post("/api/library", json={"path": str(a_file)})
    assert resp.status_code == 404


def test_switch_to_the_same_path_is_a_noop(client, fastapi_app, library: Path):
    make_jpeg(library / "04-office-software-humor" / "image" / "a.jpg")
    fastapi_app.state.services.indexer.scan()
    db_before = fastapi_app.state.services.db

    resp = client.post("/api/library", json={"path": str(library)})
    assert resp.status_code == 200
    data = resp.json()
    assert data["scan_started"] is False
    assert data["library_root"] == str(library)
    assert fastapi_app.state.services.db is db_before  # nothing was actually rebuilt


def test_switch_rejects_the_data_dir_itself(client, fastapi_app):
    data_dir = fastapi_app.state.services.data_dir
    resp = client.post("/api/library", json={"path": str(data_dir)})
    assert resp.status_code == 400


def test_get_library_shape(client, web_config: WebConfig, library: Path):
    from atomik_meme_web.services import resolve_data_dir_for_library

    resp = client.get("/api/library")
    assert resp.status_code == 200
    data = resp.json()
    assert data["library_root"] == str(library)
    assert data["data_dir"] == str(resolve_data_dir_for_library(web_config, library))
    assert isinstance(data["native_picker"], bool)
    assert data["remember"] is True
    assert data["recent"] == []


def test_switching_records_recent_and_last_library(tmp_path: Path, client, library: Path):
    lib_b = _make_library(tmp_path, "library_b")
    client.post("/api/library", json={"path": str(lib_b)})

    resp = client.get("/api/library")
    recent_paths = {r["path"] for r in resp.json()["recent"]}
    assert str(lib_b) in recent_paths


def test_delete_recent_removes_an_entry(tmp_path: Path, client, library: Path):
    lib_b = _make_library(tmp_path, "library_b")
    client.post("/api/library", json={"path": str(lib_b)})
    assert str(lib_b) in {r["path"] for r in client.get("/api/library").json()["recent"]}

    resp = client.delete("/api/library/recent", params={"path": str(lib_b)})
    assert resp.status_code == 204
    assert str(lib_b) not in {r["path"] for r in client.get("/api/library").json()["recent"]}


def test_csrf_required_for_library_post_and_delete(tmp_path: Path, library: Path):
    cfg = WebConfig(
        library=library,
        data_dir=tmp_path / "webdata",
        scan_on_start=False,
        scan_interval_min=0,
        open_browser=False,
    )
    with TestClient(create_app(cfg, dist_dir=tmp_path / "no-dist")) as bare_client:
        assert bare_client.post("/api/library", json={"path": str(library)}).status_code == 403
        assert bare_client.post("/api/library/pick").status_code == 403
        assert bare_client.post("/api/library/reveal").status_code == 403
        assert bare_client.delete("/api/library/recent", params={"path": "x"}).status_code == 403


def test_library_reveal_calls_open_path(client, fastapi_app, library: Path, monkeypatch):
    from atomik_meme_web import actions

    opened: list[Path] = []
    monkeypatch.setattr(actions, "open_path", lambda p: opened.append(p))
    resp = client.post("/api/library/reveal")
    assert resp.status_code == 200
    assert opened == [library]


def test_no_library_startup_state(tmp_path: Path):
    cfg = WebConfig(
        data_dir=tmp_path / "webdata",
        scan_on_start=False,
        scan_interval_min=0,
        open_browser=False,
    )
    with TestClient(
        create_app(cfg, library=None, dist_dir=tmp_path / "no-dist"),
        headers={"X-Atomik-Meme": "1"},
    ) as client:
        health = client.get("/api/health").json()
        assert health["library_root"] is None
        assert health["items"] == 0

        items = client.get("/api/items").json()
        assert items["items"] == []
        assert items["total"] == 0

        lib_info = client.get("/api/library").json()
        assert lib_info["library_root"] is None

        # switching away from no-library still works
        new_lib = tmp_path / "chosen"
        new_lib.mkdir()
        resp = client.post("/api/library", json={"path": str(new_lib)})
        assert resp.status_code == 200
        assert resp.json()["library_root"] == str(new_lib)


@pytest.mark.parametrize("method,path", [("post", "/api/scan"), ("post", "/api/collections")])
def test_no_library_mutating_endpoints_are_graceful(tmp_path: Path, method, path):
    cfg = WebConfig(
        data_dir=tmp_path / "webdata",
        scan_on_start=False,
        scan_interval_min=0,
        open_browser=False,
    )
    with TestClient(
        create_app(cfg, library=None, dist_dir=tmp_path / "no-dist"),
        headers={"X-Atomik-Meme": "1"},
    ) as client:
        if path == "/api/collections":
            resp = client.post(path, json={"name": "x", "query": "", "filters": {}})
        else:
            resp = getattr(client, method)(path)
        assert resp.status_code == 400
