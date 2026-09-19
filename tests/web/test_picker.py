"""Native folder picker: POST /api/library/pick. Never opens a real dialog in tests --
the subprocess-running functions are monkeypatched."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from atomik_meme_web import picker


def test_pick_returns_path_on_success(client, fastapi_app, monkeypatch):
    monkeypatch.setattr(picker, "run_folder_picker", lambda timeout: "D:\\chosen\\folder")
    resp = client.post("/api/library/pick")
    assert resp.status_code == 200
    assert resp.json() == {"path": "D:\\chosen\\folder"}


def test_pick_returns_204_on_cancel(client, monkeypatch):
    monkeypatch.setattr(picker, "run_folder_picker", lambda timeout: None)
    resp = client.post("/api/library/pick")
    assert resp.status_code == 204
    assert resp.content == b""


def test_pick_returns_501_when_tk_unavailable(client, monkeypatch):
    def _raise(timeout):
        raise picker.PickerUnavailableError("no display")

    monkeypatch.setattr(picker, "run_folder_picker", _raise)
    resp = client.post("/api/library/pick")
    assert resp.status_code == 501
    assert "detail" in resp.json()


def test_pick_returns_504_on_timeout(client, monkeypatch):
    def _raise(timeout):
        raise subprocess.TimeoutExpired(cmd="python", timeout=timeout)

    monkeypatch.setattr(picker, "run_folder_picker", _raise)
    resp = client.post("/api/library/pick")
    assert resp.status_code == 504


def test_pick_returns_409_when_already_open(client, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def _slow(timeout):
        started.set()
        release.wait(timeout=5)
        return None

    monkeypatch.setattr(picker, "run_folder_picker", _slow)

    results = []

    def _first_call():
        results.append(client.post("/api/library/pick"))

    thread = threading.Thread(target=_first_call)
    thread.start()
    assert started.wait(timeout=5)

    resp2 = client.post("/api/library/pick")
    assert resp2.status_code == 409

    release.set()
    thread.join(timeout=5)
    assert results[0].status_code == 204


def test_probe_native_picker_reflects_real_tkinter_availability():
    # This actually shells out (it's the real probe, not monkeypatched) -- just confirms it
    # returns a bool and never raises, regardless of whether Tk happens to be installed here.
    assert isinstance(picker.probe_native_picker(timeout=10.0), bool)


def test_get_library_native_picker_reflects_the_startup_probe(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from atomik_meme_web.api import create_app
    from atomik_meme_web.config import WebConfig

    lib = tmp_path / "lib"
    lib.mkdir()
    cfg = WebConfig(
        library=lib,
        data_dir=tmp_path / "webdata",
        scan_on_start=False,
        scan_interval_min=0,
        open_browser=False,
    )

    monkeypatch.setattr(picker, "probe_native_picker", lambda: True)
    with TestClient(
        create_app(cfg, dist_dir=tmp_path / "no-dist"), headers={"X-Atomik-Meme": "1"}
    ) as new_client:
        assert new_client.get("/api/library").json()["native_picker"] is True

    monkeypatch.setattr(picker, "probe_native_picker", lambda: False)
    with TestClient(
        create_app(cfg, dist_dir=tmp_path / "no-dist2"), headers={"X-Atomik-Meme": "1"}
    ) as new_client2:
        assert new_client2.get("/api/library").json()["native_picker"] is False
