"""Thumbnails (WebP, cached, dHash) and the /media endpoint (correct MIME, Range -> 206)."""

from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest
from PIL import Image

from atomik_meme.media import VideoDecodeError
from tests.web.conftest import has_ffmpeg, make_animated_gif, make_jpeg, make_mp4


def _first_item_id(client, **params) -> int:
    data = client.get("/api/items", params={"nsfw": "include", **params}).json()
    assert data["items"], "expected at least one item"
    return data["items"][0]["id"]


def _add_mp4(tmp_path: Path, dest: Path, duration_s: float = 2.0) -> None:
    """Build the synthetic mp4 in a scratch dir, then copy just the file into the library.

    `make_mp4` leaves its per-frame PNGs in a sibling `_<stem>_frames/` directory; building it
    outside the library keeps those loose PNGs from being picked up as extra "image" items.
    """
    scratch = tmp_path / "_mp4_scratch" / dest.name
    scratch.parent.mkdir(parents=True, exist_ok=True)
    make_mp4(scratch, duration_s=duration_s)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(scratch, dest)


def test_thumb_is_generated_once_and_cached(library: Path, client, fastapi_app):
    make_jpeg(library / "04-office-software-humor" / "image" / "a.jpg")
    fastapi_app.state.indexer.scan()
    item_id = _first_item_id(client)

    resp = client.get(f"/api/items/{item_id}/thumb", params={"w": 320})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/webp"
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"

    thumbs_dir = fastapi_app.state.thumbs.dir
    files = list(thumbs_dir.rglob("*_320.webp"))
    assert len(files) == 1
    first_bytes = files[0].read_bytes()

    resp2 = client.get(f"/api/items/{item_id}/thumb", params={"w": 320})
    assert resp2.status_code == 200
    files_after = list(thumbs_dir.rglob("*_320.webp"))
    assert len(files_after) == 1
    assert files_after[0].read_bytes() == first_bytes

    # dHash was computed and persisted
    row = fastapi_app.state.db.conn.execute(
        "SELECT dhash FROM items WHERE id=?", (item_id,)
    ).fetchone()
    assert row["dhash"] is not None
    assert len(row["dhash"]) == 16


def test_thumb_snaps_to_nearest_configured_size(library: Path, client, fastapi_app):
    """`w=99` must snap to a configured `thumb_sizes` value (default [320, 640]) -- a hostile
    caller spamming distinct widths can't force unbounded distinct thumbnail files."""
    make_jpeg(library / "04-office-software-humor" / "image" / "a.jpg")
    fastapi_app.state.indexer.scan()
    item_id = _first_item_id(client)

    resp = client.get(f"/api/items/{item_id}/thumb", params={"w": 99})
    assert resp.status_code == 200

    thumbs_dir = fastapi_app.state.thumbs.dir
    names = {f.name for f in thumbs_dir.rglob("*.webp")}
    assert any(name.endswith("_320.webp") for name in names)
    assert not any(name.endswith("_99.webp") for name in names)


def test_thumb_generates_all_configured_sizes_from_a_single_decode(
    library: Path, client, fastapi_app, monkeypatch
):
    """One (mocked) video-frame extraction must produce every configured size (320 and 640)."""
    # The automatic post-scan prewarm would otherwise race the monkeypatch below and consume
    # the very first (real, failing) decode attempt before this test ever gets to observe it.
    monkeypatch.setattr(fastapi_app.state.thumbs, "prewarm_async", lambda *a, **kw: None)
    video_path = library / "04-office-software-humor" / "image" / "a.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"not a real mp4 -- decoding is monkeypatched below")
    fastapi_app.state.indexer.scan()
    item_id = _first_item_id(client, type="video")

    calls: list[float] = []

    def fake_extract_frame(path, timestamp, *args, **kwargs):
        calls.append(timestamp)
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (10, 20, 30)).save(buf, format="JPEG")
        return buf.getvalue()

    monkeypatch.setattr("atomik_meme_web.thumbs.extract_frame", fake_extract_frame)

    resp = client.get(f"/api/items/{item_id}/thumb", params={"w": 320})
    assert resp.status_code == 200
    assert len(calls) == 1

    sha = fastapi_app.state.db.conn.execute(
        "SELECT sha256 FROM items WHERE id=?", (item_id,)
    ).fetchone()["sha256"]
    thumbs_dir = fastapi_app.state.thumbs.dir
    assert (thumbs_dir / sha[:2] / f"{sha}_320.webp").is_file()
    assert (thumbs_dir / sha[:2] / f"{sha}_640.webp").is_file()


def test_thumb_failure_is_not_retried_within_the_cooldown(
    library: Path, client, fastapi_app, monkeypatch
):
    monkeypatch.setattr(fastapi_app.state.thumbs, "prewarm_async", lambda *a, **kw: None)
    video_path = library / "04-office-software-humor" / "image" / "a.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"not a real mp4 -- decoding is monkeypatched below")
    fastapi_app.state.indexer.scan()
    item_id = _first_item_id(client, type="video")

    calls: list[float] = []

    def failing_extract_frame(path, timestamp, *args, **kwargs):
        calls.append(timestamp)
        raise VideoDecodeError("simulated decode failure")

    monkeypatch.setattr("atomik_meme_web.thumbs.extract_frame", failing_extract_frame)

    resp1 = client.get(f"/api/items/{item_id}/thumb")
    assert resp1.status_code == 200  # a placeholder image, not an error response
    resp2 = client.get(f"/api/items/{item_id}/thumb")
    assert resp2.status_code == 200
    assert len(calls) == 1, "the second request should be short-circuited by the failure cooldown"


def test_thumb_by_sha256_alt_route(library: Path, client, fastapi_app):
    make_jpeg(library / "04-office-software-humor" / "image" / "a.jpg")
    fastapi_app.state.indexer.scan()
    row = fastapi_app.state.db.conn.execute("SELECT sha256 FROM items").fetchone()
    resp = client.get(f"/api/thumbs/{row['sha256']}_320.webp")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/webp"


def test_gif_thumb_generation(library: Path, client, fastapi_app):
    make_animated_gif(library / "02-relationship-drama-comics" / "gif" / "a.gif")
    fastapi_app.state.indexer.scan()
    item_id = _first_item_id(client)
    resp = client.get(f"/api/items/{item_id}/thumb")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/webp"


@pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg/ffprobe not available")
def test_video_thumb_generation(tmp_path: Path, library: Path, client, fastapi_app):
    _add_mp4(tmp_path, library / "04-office-software-humor" / "image" / "a.mp4")
    fastapi_app.state.indexer.scan()
    item_id = _first_item_id(client, type="video")
    resp = client.get(f"/api/items/{item_id}/thumb")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/webp"


def test_media_endpoint_correct_mime(library: Path, client, fastapi_app):
    make_jpeg(library / "04-office-software-humor" / "image" / "a.jpg")
    fastapi_app.state.indexer.scan()
    item_id = _first_item_id(client)
    resp = client.get(f"/api/items/{item_id}/media")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert "inline" in resp.headers.get("content-disposition", "")
    assert resp.headers.get("accept-ranges") == "bytes"


@pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg/ffprobe not available")
def test_media_range_request_returns_206(tmp_path: Path, library: Path, client, fastapi_app):
    _add_mp4(tmp_path, library / "04-office-software-humor" / "image" / "a.mp4")
    fastapi_app.state.indexer.scan()
    item_id = _first_item_id(client, type="video")
    full = client.get(f"/api/items/{item_id}/media")
    assert full.status_code == 200
    total_len = len(full.content)
    assert total_len > 10

    resp = client.get(f"/api/items/{item_id}/media", headers={"Range": "bytes=0-9"})
    assert resp.status_code == 206
    assert resp.headers["content-type"] == "video/mp4"
    assert len(resp.content) == 10
    assert resp.headers["content-range"] == f"bytes 0-9/{total_len}"


def test_media_404_for_unknown_id(client):
    resp = client.get("/api/items/999999/media")
    assert resp.status_code == 404
