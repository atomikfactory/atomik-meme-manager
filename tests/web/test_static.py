"""Static SPA serving: friendly placeholder when web/dist is missing, fallback when present."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from atomik_meme_web.api import create_app
from atomik_meme_web.config import WebConfig


def test_missing_dist_shows_friendly_message(tmp_path: Path, library: Path):
    cfg = WebConfig(
        library=library,
        data_dir=tmp_path / "webdata",
        scan_on_start=False,
        scan_interval_min=0,
        open_browser=False,
    )
    with TestClient(create_app(cfg, dist_dir=tmp_path / "no-such-dist")) as client:
        resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "build-web.ps1" in resp.text
    assert "/api/health" in resp.text


def test_api_routes_still_work_when_dist_is_missing(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200


def test_spa_fallback_serves_built_index(tmp_path: Path, library: Path):
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<html><body>the app</body></html>", encoding="utf-8")
    assets_dir = dist_dir / "assets"
    assets_dir.mkdir()
    (assets_dir / "main.js").write_text("console.log('hi')", encoding="utf-8")

    cfg = WebConfig(
        library=library,
        data_dir=tmp_path / "webdata",
        scan_on_start=False,
        scan_interval_min=0,
        open_browser=False,
    )
    with TestClient(create_app(cfg, dist_dir=dist_dir)) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert "the app" in root.text

        asset = client.get("/assets/main.js")
        assert asset.status_code == 200
        assert "console.log" in asset.text

        deep_link = client.get("/gallery/some-item")
        assert deep_link.status_code == 200
        assert "the app" in deep_link.text

        # /api routes must still take priority over the catch-all
        health = client.get("/api/health")
        assert health.status_code == 200
