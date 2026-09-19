from __future__ import annotations

import logging
from pathlib import Path

import pytest

from atomik_meme_web.config import WebConfig, WebConfigError, load_web_config


def test_defaults_without_any_config(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("APPDATA", raising=False)
    cfg = load_web_config(None)
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8765
    assert cfg.open_browser is True
    assert cfg.scan_on_start is True
    assert cfg.scan_interval_min == 5
    assert cfg.nsfw_default == "hide"
    assert cfg.thumb_sizes == [320, 640]
    assert cfg.extensions.image[:2] == [".jpg", ".jpeg"]
    assert ".meme-web" in cfg.ignore
    # default library `./output` resolved relative to cwd
    assert cfg.library == (tmp_path / "output").resolve()


def test_web_section_is_read_from_atomik_meme_yaml(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "atomik-meme.yaml").write_text(
        """
ollama:
  host: http://127.0.0.1:11434
web:
  library: ./mylib
  port: 9001
  nsfw_default: show
  thumb_sizes: [200]
  scan_interval_min: 0
""",
        encoding="utf-8",
    )
    cfg = load_web_config(None)
    assert cfg.library == (tmp_path / "mylib").resolve()
    assert cfg.port == 9001
    assert cfg.nsfw_default == "show"
    assert cfg.thumb_sizes == [200]
    assert cfg.scan_interval_min == 0
    # untouched keys keep their defaults
    assert cfg.host == "127.0.0.1"


def test_cli_overrides_win_over_file(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "atomik-meme.yaml").write_text("web:\n  port: 9001\n", encoding="utf-8")
    cfg = load_web_config(None, port=1234, host="0.0.0.0")
    assert cfg.port == 1234
    assert cfg.host == "0.0.0.0"


def test_explicit_config_path(tmp_path: Path):
    config_path = tmp_path / "custom.yaml"
    config_path.write_text("web:\n  port: 7777\n", encoding="utf-8")
    cfg = load_web_config(config_path)
    assert cfg.port == 7777


def test_invalid_web_section_raises(tmp_path: Path):
    config_path = tmp_path / "bad.yaml"
    config_path.write_text("web: not-a-mapping\n", encoding="utf-8")
    with pytest.raises(WebConfigError):
        load_web_config(config_path)


def test_loading_web_section_emits_no_warning_in_either_loader(tmp_path: Path, caplog):
    """`web:` must be a recognised, silently-ignored key for the `atomik-meme` CLI's own loader
    (see atomik_meme.config.Settings.web) -- and atomik-meme-web's own loader must not warn
    either."""
    config_path = tmp_path / "shared.yaml"
    config_path.write_text(
        "ollama:\n  timeout_s: 42\nweb:\n  port: 9001\n  nsfw_default: show\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        cfg = load_web_config(config_path)
    assert cfg.port == 9001
    assert cfg.nsfw_default == "show"
    assert not any("web" in record.message for record in caplog.records)


def test_resolved_data_dir_defaults_under_library(tmp_path: Path):
    cfg = WebConfig(library=tmp_path / "lib")
    assert cfg.resolved_data_dir() == tmp_path / "lib" / ".meme-web"


def test_resolved_data_dir_explicit(tmp_path: Path):
    cfg = WebConfig(library=tmp_path / "lib", data_dir=tmp_path / "elsewhere")
    assert cfg.resolved_data_dir() == tmp_path / "elsewhere"
