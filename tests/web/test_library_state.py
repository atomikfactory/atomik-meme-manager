"""State file: read/write, `remember_library: false`, and startup precedence
(flag > last_library > web.library > ./output > no library)."""

from __future__ import annotations

from pathlib import Path

from atomik_meme_web.config import WebConfig
from atomik_meme_web.library_state import (
    load_state,
    record_library_opened,
    remove_recent,
    resolve_startup_library,
    save_state,
    state_file_path,
)


def test_state_file_path_is_redirected_for_tests(tmp_path: Path):
    # The autouse isolate_environment fixture points LOCALAPPDATA at a tmp dir; confirm we
    # actually read that env var rather than hardcoding the real one.
    path = state_file_path()
    assert "localappdata-unused" in str(path) or "xdg-state-unused" in str(path)


def test_save_and_load_state_roundtrip(tmp_path: Path):
    assert load_state().last_library is None
    record_library_opened(tmp_path / "lib1")
    state = load_state()
    assert state.last_library == str(tmp_path / "lib1")
    assert state.recent[0].path == str(tmp_path / "lib1")


def test_record_library_opened_dedupes_and_caps_recent(tmp_path: Path):
    for i in range(15):
        record_library_opened(tmp_path / f"lib{i}")
    state = load_state()
    assert len(state.recent) == 10
    assert state.recent[0].path == str(tmp_path / "lib14")

    # opening an already-recent path moves it to the front instead of duplicating it
    record_library_opened(tmp_path / "lib5")
    state2 = load_state()
    paths = [r.path for r in state2.recent]
    assert paths.count(str(tmp_path / "lib5")) == 1
    assert paths[0] == str(tmp_path / "lib5")


def test_remove_recent(tmp_path: Path):
    record_library_opened(tmp_path / "lib1")
    record_library_opened(tmp_path / "lib2")
    remove_recent(str(tmp_path / "lib1"))
    state = load_state()
    assert str(tmp_path / "lib1") not in {r.path for r in state.recent}
    assert str(tmp_path / "lib2") in {r.path for r in state.recent}


def test_corrupt_state_file_is_ignored_not_fatal(tmp_path: Path):
    path = state_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    state = load_state()
    assert state.last_library is None
    assert state.recent == []


def test_precedence_cli_flag_wins_over_everything(tmp_path: Path):
    lib_flag = tmp_path / "from_flag"
    lib_flag.mkdir()
    lib_state = tmp_path / "from_state"
    lib_state.mkdir()
    record_library_opened(lib_state)
    cfg = WebConfig(library=tmp_path / "from_config")
    resolved = resolve_startup_library(cfg, cli_library=lib_flag)
    assert resolved == lib_flag.resolve()


def test_precedence_last_library_wins_over_config_when_it_exists(tmp_path: Path):
    lib_state = tmp_path / "from_state"
    lib_state.mkdir()
    record_library_opened(lib_state)
    cfg = WebConfig(library=tmp_path / "from_config")  # doesn't exist
    resolved = resolve_startup_library(cfg, cli_library=None)
    assert resolved == lib_state


def test_precedence_falls_through_when_last_library_no_longer_exists(tmp_path: Path):
    lib_state = tmp_path / "from_state_gone"
    record_library_opened(lib_state)  # never actually created on disk
    lib_config = tmp_path / "from_config"
    lib_config.mkdir()
    cfg = WebConfig(library=lib_config)
    resolved = resolve_startup_library(cfg, cli_library=None)
    assert resolved == lib_config


def test_precedence_config_library_used_when_no_state(tmp_path: Path):
    lib_config = tmp_path / "from_config"
    lib_config.mkdir()
    cfg = WebConfig(library=lib_config)
    resolved = resolve_startup_library(cfg, cli_library=None)
    assert resolved == lib_config


def test_precedence_no_library_when_nothing_resolves(tmp_path: Path):
    cfg = WebConfig(library=tmp_path / "does-not-exist")
    resolved = resolve_startup_library(cfg, cli_library=None)
    assert resolved is None


def test_remember_library_false_ignores_state_and_writes_nothing(tmp_path: Path, monkeypatch):
    lib_state = tmp_path / "from_state"
    lib_state.mkdir()
    record_library_opened(lib_state)
    lib_config = tmp_path / "from_config"
    lib_config.mkdir()
    cfg = WebConfig(library=lib_config, remember_library=False)

    resolved = resolve_startup_library(cfg, cli_library=None)
    assert resolved == lib_config  # last_library ignored despite existing

    # And nothing new gets persisted through the app when remember_library is False -- verified
    # at the API layer in test_library.py; here we just confirm state.json wasn't touched by
    # resolve_startup_library itself.
    path = state_file_path()
    before = path.read_bytes() if path.is_file() else None
    resolve_startup_library(cfg, cli_library=None)
    after = path.read_bytes() if path.is_file() else None
    assert before == after


def test_remember_library_false_switch_does_not_persist(tmp_path: Path, library, web_config):
    from fastapi.testclient import TestClient

    from atomik_meme_web.api import create_app

    web_config.remember_library = False
    with TestClient(
        create_app(web_config, dist_dir=tmp_path / "no-dist"), headers={"X-Atomik-Meme": "1"}
    ) as client:
        new_lib = tmp_path / "chosen"
        new_lib.mkdir()
        resp = client.post("/api/library", json={"path": str(new_lib)})
        assert resp.status_code == 200

    assert load_state().last_library is None


def test_save_state_is_atomic_write(tmp_path: Path):
    from atomik_meme_web.library_state import LibraryState

    save_state(LibraryState(last_library=str(tmp_path)))
    path = state_file_path()
    assert path.is_file()
    assert not path.with_name(path.name + ".tmp").exists()
