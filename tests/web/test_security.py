"""CSRF guard, the global exception handler, and the `q` length/term cap."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from atomik_meme_web.api import create_app
from atomik_meme_web.config import WebConfig
from atomik_meme_web.search import MAX_QUERY_LENGTH, MAX_QUERY_TERMS
from tests.web.conftest import make_jpeg


def test_post_without_csrf_header_is_rejected(tmp_path: Path, library: Path):
    cfg = WebConfig(
        library=library,
        data_dir=tmp_path / "webdata",
        scan_on_start=False,
        scan_interval_min=0,
        open_browser=False,
    )
    with TestClient(create_app(cfg, dist_dir=tmp_path / "no-dist")) as bare_client:
        resp = bare_client.post("/api/scan")
        assert resp.status_code == 403
        assert resp.json() == {"detail": "missing X-Atomik-Meme header"}


def test_post_with_csrf_header_works(client):
    resp = client.post("/api/scan")
    assert resp.status_code in (202, 409)


def test_put_and_delete_without_header_are_rejected(tmp_path: Path, library: Path):
    cfg = WebConfig(
        library=library,
        data_dir=tmp_path / "webdata",
        scan_on_start=False,
        scan_interval_min=0,
        open_browser=False,
    )
    with TestClient(create_app(cfg, dist_dir=tmp_path / "no-dist")) as bare_client:
        assert bare_client.put("/api/items/1/favorite", json={"favorite": True}).status_code == 403
        assert bare_client.delete("/api/collections/1").status_code == 403


def test_get_routes_unaffected_by_missing_header(tmp_path: Path, library: Path):
    cfg = WebConfig(
        library=library,
        data_dir=tmp_path / "webdata",
        scan_on_start=False,
        scan_interval_min=0,
        open_browser=False,
    )
    with TestClient(create_app(cfg, dist_dir=tmp_path / "no-dist")) as bare_client:
        assert bare_client.get("/api/health").status_code == 200
        assert bare_client.get("/api/items").status_code == 200
        assert bare_client.get("/api/scan/status").status_code == 200


def test_mismatched_origin_is_rejected_even_with_header(tmp_path: Path, library: Path):
    cfg = WebConfig(
        library=library,
        data_dir=tmp_path / "webdata",
        scan_on_start=False,
        scan_interval_min=0,
        open_browser=False,
    )
    with TestClient(create_app(cfg, dist_dir=tmp_path / "no-dist")) as bare_client:
        resp = bare_client.post(
            "/api/scan",
            headers={"X-Atomik-Meme": "1", "Origin": "https://evil.example.com"},
        )
        assert resp.status_code == 403
        assert resp.json() == {"detail": "origin not allowed"}


def test_matching_own_origin_is_accepted(tmp_path: Path, library: Path):
    cfg = WebConfig(
        library=library,
        data_dir=tmp_path / "webdata",
        host="127.0.0.1",
        port=8765,
        scan_on_start=False,
        scan_interval_min=0,
        open_browser=False,
    )
    with TestClient(create_app(cfg, dist_dir=tmp_path / "no-dist")) as bare_client:
        resp = bare_client.post(
            "/api/scan",
            headers={"X-Atomik-Meme": "1", "Origin": "http://127.0.0.1:8765"},
        )
        assert resp.status_code in (202, 409)


def test_dev_origin_is_accepted(tmp_path: Path, library: Path):
    cfg = WebConfig(
        library=library,
        data_dir=tmp_path / "webdata",
        scan_on_start=False,
        scan_interval_min=0,
        open_browser=False,
    )
    with TestClient(create_app(cfg, dist_dir=tmp_path / "no-dist")) as bare_client:
        resp = bare_client.post(
            "/api/scan",
            headers={"X-Atomik-Meme": "1", "Origin": "http://localhost:5173"},
        )
        assert resp.status_code in (202, 409)


def test_unhandled_exception_becomes_json_500(tmp_path: Path, library: Path, monkeypatch):
    cfg = WebConfig(
        library=library,
        data_dir=tmp_path / "webdata",
        scan_on_start=False,
        scan_interval_min=0,
        open_browser=False,
    )
    fastapi_app = create_app(cfg, dist_dir=tmp_path / "no-dist")

    def _boom(self):
        raise RuntimeError("boom")

    # `conn` is a class-level `@property`; patch the class descriptor, not the instance.
    monkeypatch.setattr(type(fastapi_app.state.db), "conn", property(_boom))
    # `raise_server_exceptions=False`: a real HTTP client only ever sees the response our
    # `@app.exception_handler(Exception)` produces, never the raw Python exception -- the
    # TestClient's default of re-raising it is purely a debugging convenience for test authors.
    with TestClient(fastapi_app, raise_server_exceptions=False) as raw_client:
        resp = raw_client.get("/api/health")
    assert resp.status_code == 500
    assert resp.json() == {"detail": "internal server error"}


def test_query_too_long_is_rejected(client):
    resp = client.get("/api/items", params={"q": "a" * (MAX_QUERY_LENGTH + 1)})
    assert resp.status_code == 400
    assert "detail" in resp.json()


def test_query_with_too_many_terms_is_rejected(client):
    q = " ".join(f"term{i}" for i in range(MAX_QUERY_TERMS + 1))
    resp = client.get("/api/items", params={"q": q})
    assert resp.status_code == 400


def test_query_within_limits_is_accepted(client, fastapi_app, library):
    make_jpeg(library / "04-office-software-humor" / "image" / "a.jpg")
    fastapi_app.state.indexer.scan()
    q = " ".join(f"term{i}" for i in range(MAX_QUERY_TERMS))
    resp = client.get("/api/items", params={"q": q})
    assert resp.status_code == 200
