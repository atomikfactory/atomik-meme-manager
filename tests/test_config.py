import logging
from pathlib import Path

import pytest
import yaml

from atomik_meme.config import (
    Settings,
    SettingsError,
    load_settings,
    normalize_ollama_host,
    write_sample_config,
)


def test_defaults_match_design_doc(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "no-such-appdata"))
    settings = load_settings(None)
    assert settings.ollama.host == "http://127.0.0.1:11434"
    assert settings.ollama.timeout_s == 180
    assert settings.image.model_max_pixels == 1_200_000
    assert settings.image.tall_image.max_total_pixels == 4_500_000
    assert settings.categorize.category_count == 10
    assert settings.categorize.others_name == "others"
    assert settings.source == "<built-in defaults>"

    # v2 additions
    assert settings.processing.remove_from_input is True  # on by default since 2026-09-17
    assert settings.processing.video_extensions == [".mp4", ".webm", ".mov", ".mkv", ".m4v", ".avi"]
    assert settings.media.ffmpeg_path == "ffmpeg"
    assert settings.media.ffprobe_path == "ffprobe"
    assert settings.media.frames_per_video == 6
    assert settings.media.frames_per_gif == 6
    assert settings.media.max_video_duration_s == 180
    assert settings.media.frame_max_total_pixels == 4_500_000
    assert settings.categorize.nsfw.enabled is True
    assert settings.categorize.nsfw.name == "nsfw"
    assert settings.categorize.nsfw.folder == "00 - NSFW"


def test_explicit_config_path_deep_merges_partial_overrides(tmp_path: Path):
    config_path = tmp_path / "custom.yaml"
    config_path.write_text(
        "ollama:\n  timeout_s: 42\n",
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    assert settings.ollama.timeout_s == 42
    # everything else should still be default
    assert settings.ollama.host == "http://127.0.0.1:11434"
    assert settings.categorize.assign_batch_size == 12
    assert settings.source == str(config_path)


def test_missing_explicit_config_raises(tmp_path: Path):
    with pytest.raises(SettingsError):
        load_settings(tmp_path / "does-not-exist.yaml")


def test_invalid_yaml_raises(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("ollama: [this is not a mapping", encoding="utf-8")
    with pytest.raises(SettingsError):
        load_settings(bad)


def test_wrong_type_raises_settings_error(tmp_path: Path):
    bad = tmp_path / "bad_types.yaml"
    bad.write_text("processing:\n  workers: not-a-number\n", encoding="utf-8")
    with pytest.raises(SettingsError):
        load_settings(bad)


def test_unknown_key_warns_but_does_not_raise(tmp_path: Path, caplog):
    config_path = tmp_path / "with_unknown.yaml"
    config_path.write_text("ollama:\n  totally_unknown_key: 1\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        settings = load_settings(config_path)
    assert isinstance(settings, Settings)
    assert any("totally_unknown_key" in record.message for record in caplog.records)


def test_web_section_is_recognised_and_ignored_without_warning(tmp_path: Path, caplog):
    """`web:` belongs to `atomik-meme-web` (see atomik_meme_web.config), but the CLI's own loader
    must treat it as a normal, silently-ignored field -- not an "unknown config key" warning."""
    config_path = tmp_path / "with_web.yaml"
    config_path.write_text(
        "ollama:\n  timeout_s: 42\nweb:\n  port: 9001\n  nsfw_default: show\n",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING):
        settings = load_settings(config_path)
    assert settings.ollama.timeout_s == 42
    assert settings.web == {"port": 9001, "nsfw_default": "show"}
    assert not any("web" in record.message for record in caplog.records)


def test_env_ollama_host_override_without_scheme(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11435")
    settings = load_settings(None)
    assert settings.ollama.host == "http://127.0.0.1:11435"


def test_normalize_ollama_host():
    assert normalize_ollama_host("127.0.0.1:11435") == "http://127.0.0.1:11435"
    assert normalize_ollama_host("http://example.com:1234/") == "http://example.com:1234"
    assert normalize_ollama_host("https://example.com") == "https://example.com"


def test_write_sample_config_is_loadable_and_matches_defaults(tmp_path: Path):
    out_path = tmp_path / "atomik-meme.yaml"
    write_sample_config(out_path)
    assert out_path.is_file()
    raw = yaml.safe_load(out_path.read_text(encoding="utf-8"))
    assert raw["image"]["model_max_pixels"] == 1_200_000  # not the invalid `1_200_000` literal
    loaded = load_settings(out_path)
    defaults = Settings()
    assert loaded.ollama.host == defaults.ollama.host
    assert loaded.categorize.category_count == defaults.categorize.category_count
