"""`atomik-meme-web index` CLI: one-off scan, clear errors for a missing library, `--rebuild`."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from atomik_meme_web.cli import app
from atomik_meme_web.config import load_web_config
from atomik_meme_web.db import Database
from atomik_meme_web.services import resolve_data_dir_for_library
from tests.web.conftest import make_jpeg

runner = CliRunner()


def test_index_command_scans_and_prints_counts(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APPDATA", raising=False)
    library = tmp_path / "output"
    (library / "04-office-software-humor" / "image").mkdir(parents=True)
    make_jpeg(library / "04-office-software-humor" / "image" / "a.jpg")

    result = runner.invoke(app, ["index"])
    assert result.exit_code == 0, result.output
    assert "added=1" in result.output
    assert "modified=0" in result.output
    assert "removed=0" in result.output
    assert "moved=0" in result.output


def test_index_command_missing_library_is_a_clear_error(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APPDATA", raising=False)
    result = runner.invoke(app, ["index", "--library", str(tmp_path / "nope")])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


def test_default_library_used_when_no_flag_given(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APPDATA", raising=False)
    # no ./output directory exists here, no state file, no web.library -> clear error, not a crash
    result = runner.invoke(app, ["index"])
    assert result.exit_code == 1
    assert "no library found" in result.output.lower()


def test_index_rebuild_preserves_favorites(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APPDATA", raising=False)
    library = tmp_path / "output"
    (library / "04-office-software-humor" / "image").mkdir(parents=True)
    make_jpeg(library / "04-office-software-humor" / "image" / "a.jpg")
    data_dir = tmp_path / "webdata"

    result1 = runner.invoke(app, ["index", "--data-dir", str(data_dir)])
    assert result1.exit_code == 0, result1.output
    assert "added=1" in result1.output

    # `--data-dir` is explicit, so the index lives under a per-library hash subfolder of it
    # (services.resolve_data_dir_for_library) -- never bare `<data_dir>/index.db`.
    cfg = load_web_config(None, data_dir=data_dir)
    actual_data_dir = resolve_data_dir_for_library(cfg, library)
    db = Database(actual_data_dir / "index.db")
    row = db.conn.execute("SELECT sha256 FROM items").fetchone()
    sha = row["sha256"]
    with db.write() as conn:
        conn.execute(
            "INSERT INTO favorites (sha256, created_at) VALUES (?, ?)",
            (sha, "2026-01-01T00:00:00Z"),
        )
    db.close()

    result2 = runner.invoke(app, ["index", "--data-dir", str(data_dir), "--rebuild"])
    assert result2.exit_code == 0, result2.output
    assert "added=1" in result2.output  # items table was wiped, so the file is "new" again

    db2 = Database(actual_data_dir / "index.db")
    fav = db2.conn.execute("SELECT sha256 FROM favorites WHERE sha256=?", (sha,)).fetchone()
    assert fav is not None, "favorites must survive --rebuild"
    # the re-added item still has the same sha256, so the API's join back onto favorites works
    row2 = db2.conn.execute("SELECT id FROM items WHERE sha256=?", (sha,)).fetchone()
    assert row2 is not None
