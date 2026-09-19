"""Inbox drop (sanitised names, no overwrite, 403 when disabled) and open/reveal (monkeypatched)."""

from __future__ import annotations

import io
from pathlib import Path

from atomik_meme_web import actions
from atomik_meme_web.api import create_app
from atomik_meme_web.config import WebConfig
from tests.web.conftest import make_jpeg


def test_inbox_disabled_returns_403(tmp_path: Path, library: Path):
    cfg = WebConfig(
        library=library,
        data_dir=tmp_path / "webdata",
        inbox_dir=None,
        scan_on_start=False,
        scan_interval_min=0,
        open_browser=False,
    )
    from fastapi.testclient import TestClient

    with TestClient(create_app(cfg, dist_dir=tmp_path / "no-dist")) as client:
        resp = client.post(
            "/api/inbox", files={"files": ("meme.jpg", io.BytesIO(b"data"), "image/jpeg")}
        )
    assert resp.status_code == 403


def test_inbox_enabled_saves_sanitised_and_never_overwrites(client, web_config: WebConfig):
    inbox_dir = web_config.inbox_dir
    resp1 = client.post(
        "/api/inbox",
        files={"files": ("..\\..\\evil name?.jpg", io.BytesIO(b"one"), "image/jpeg")},
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["inbox_dir"] == str(inbox_dir)
    assert len(data1["saved"]) == 1
    saved_name = data1["saved"][0]
    assert "?" not in saved_name and "\\" not in saved_name and ".." not in saved_name
    assert (inbox_dir / saved_name).read_bytes() == b"one"

    # a second upload with the exact same sanitised name must not overwrite the first
    resp2 = client.post(
        "/api/inbox", files={"files": (saved_name, io.BytesIO(b"two"), "image/jpeg")}
    )
    data2 = resp2.json()
    saved_name2 = data2["saved"][0]
    assert saved_name2 != saved_name
    assert (inbox_dir / saved_name).read_bytes() == b"one"
    assert (inbox_dir / saved_name2).read_bytes() == b"two"


def test_sanitize_inbox_filename_rejects_empty_or_dotonly():
    import pytest

    with pytest.raises(actions.InvalidInboxFilenameError):
        actions.sanitize_inbox_filename("")
    with pytest.raises(actions.InvalidInboxFilenameError):
        actions.sanitize_inbox_filename("...")
    with pytest.raises(actions.InvalidInboxFilenameError):
        actions.sanitize_inbox_filename("   ")


def test_sanitize_inbox_filename_strips_path_and_control_chars():
    assert actions.sanitize_inbox_filename("C:\\dir\\name.jpg") == "name.jpg"
    assert actions.sanitize_inbox_filename("../../etc/passwd") == "passwd"
    assert actions.sanitize_inbox_filename("bad\x00name.jpg") == "badname.jpg"


def test_sanitize_inbox_filename_rejects_windows_reserved_device_names():
    import pytest

    for name in ("CON", "con.txt", "COM1", "com3.tar.gz", "LPT9", "NUL", "PRN.jpg", "AUX"):
        with pytest.raises(actions.InvalidInboxFilenameError):
            actions.sanitize_inbox_filename(name)
    # a name that merely starts with a reserved prefix is fine
    assert actions.sanitize_inbox_filename("CONcept.jpg") == "CONcept.jpg"


def test_unique_destination_atomically_reserves_the_path(tmp_path: Path):
    """`unique_destination` must actually create (reserve) the file it returns, not just
    compute a name -- otherwise two concurrent callers could both "win" the same candidate."""
    first = actions.unique_destination(tmp_path, "meme.jpg")
    assert first.is_file()  # reserved: an empty placeholder already exists
    assert first.read_bytes() == b""

    second = actions.unique_destination(tmp_path, "meme.jpg")
    assert second != first
    assert second.name == "meme-1.jpg"
    assert second.is_file()


def test_inbox_reports_per_file_failures_without_500ing_the_batch(client, web_config: WebConfig):
    resp = client.post(
        "/api/inbox",
        files=[
            ("files", ("good.jpg", io.BytesIO(b"data"), "image/jpeg")),
            ("files", ("CON.txt", io.BytesIO(b"data"), "text/plain")),
        ],
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["saved"] == ["good.jpg"]
    assert len(data["failed"]) == 1
    assert data["failed"][0]["name"] == "CON.txt"
    assert "error" in data["failed"][0]


def test_open_and_reveal_are_never_actually_launched(
    client, fastapi_app, library: Path, monkeypatch
):
    make_jpeg(library / "04-office-software-humor" / "image" / "a.jpg")
    fastapi_app.state.indexer.scan()
    item_id = client.get("/api/items", params={"nsfw": "include"}).json()["items"][0]["id"]

    opened: list[Path] = []
    revealed: list[Path] = []
    monkeypatch.setattr(actions, "open_path", lambda p: opened.append(p))
    monkeypatch.setattr(actions, "reveal_path", lambda p: revealed.append(p))

    resp_open = client.post(f"/api/items/{item_id}/open")
    assert resp_open.status_code == 200
    assert resp_open.json() == {"ok": True}
    assert len(opened) == 1

    resp_reveal = client.post(f"/api/items/{item_id}/reveal")
    assert resp_reveal.status_code == 200
    assert len(revealed) == 1


def test_open_404_for_unknown_item(client, monkeypatch):
    monkeypatch.setattr(actions, "open_path", lambda p: None)
    resp = client.post("/api/items/999999/open")
    assert resp.status_code == 404


def test_open_and_reveal_404_when_file_deleted_since_last_scan(
    client, fastapi_app, library: Path, monkeypatch
):
    """A file removed from disk after the last scan (before the next one runs) must not 500
    `os.startfile`/`explorer` with a path that no longer exists."""
    # Avoid the automatic post-scan prewarm racing to open (and, on Windows, transiently lock)
    # this exact file right as the test tries to delete it.
    monkeypatch.setattr(fastapi_app.state.thumbs, "prewarm_async", lambda *a, **kw: None)
    media = library / "04-office-software-humor" / "image" / "a.jpg"
    make_jpeg(media)
    fastapi_app.state.indexer.scan()
    item_id = client.get("/api/items", params={"nsfw": "include"}).json()["items"][0]["id"]

    media.unlink()

    opened: list[Path] = []
    revealed: list[Path] = []
    monkeypatch.setattr(actions, "open_path", lambda p: opened.append(p))
    monkeypatch.setattr(actions, "reveal_path", lambda p: revealed.append(p))

    resp_open = client.post(f"/api/items/{item_id}/open")
    assert resp_open.status_code == 404
    assert resp_open.json() == {"detail": "media file missing on disk"}
    assert opened == []

    resp_reveal = client.post(f"/api/items/{item_id}/reveal")
    assert resp_reveal.status_code == 404
    assert resp_reveal.json() == {"detail": "media file missing on disk"}
    assert revealed == []
