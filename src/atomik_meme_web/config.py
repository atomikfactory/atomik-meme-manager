"""`WebConfig`: atomik-meme-web's settings, loaded from atomik-meme.yaml's `web:` section.

`WebConfig` itself (the model below) is independent of `atomik_meme.config.Settings` -- it owns
the actual `web:` schema. But *loading* delegates entirely to `atomik_meme.config.load_settings`
(the same function the `atomik-meme` CLI uses): that function does the file discovery, YAML
parsing, and defaults-merging, and `Settings.web` is a declared (if opaque) field there purely so
the CLI doesn't warn about an "unknown" `web` key. Resolution order mirrors the CLI's: explicit
`--config` path -> `./atomik-meme.yaml` -> `%APPDATA%/atomik-meme/config.yaml` (or
`~/.config/atomik-meme/config.yaml`) -> built-in defaults. CLI flags always win over the file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from atomik_meme.config import SettingsError as AtomikMemeSettingsError
from atomik_meme.config import load_settings as load_atomik_meme_settings

APP_NAME = "atomik-meme"

DEFAULT_IMAGE_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"]
DEFAULT_GIF_EXTENSIONS = [".gif"]
DEFAULT_VIDEO_EXTENSIONS = [".mp4", ".webm", ".mov", ".mkv", ".m4v", ".avi"]
DEFAULT_IGNORE = [".meme-manager", ".meme-web", "node_modules", ".git"]
DEFAULT_THUMB_SIZES = [320, 640]
DEFAULT_NSFW_CATEGORY_FOLDER = "00 - NSFW"


class WebConfigError(Exception):
    """Raised when the config file is malformed."""


class ExtensionsConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    image: list[str] = Field(default_factory=lambda: list(DEFAULT_IMAGE_EXTENSIONS))
    gif: list[str] = Field(default_factory=lambda: list(DEFAULT_GIF_EXTENSIONS))
    video: list[str] = Field(default_factory=lambda: list(DEFAULT_VIDEO_EXTENSIONS))

    def all_lower(self) -> set[str]:
        return {e.lower() for e in (*self.image, *self.gif, *self.video)}


class WebConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    library: Path = Path("./output")
    data_dir: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser: bool = True
    scan_on_start: bool = True
    scan_interval_min: int = 5
    inbox_dir: Path | None = Path("./input")
    nsfw_default: Literal["hide", "blur", "show"] = "hide"
    thumb_sizes: list[int] = Field(default_factory=lambda: list(DEFAULT_THUMB_SIZES))
    # Cooldown before retrying a thumbnail that failed to generate (e.g. a corrupt/unreadable
    # file), so a hostile or broken request can't force an ffmpeg/decode attempt on every hit.
    thumb_retry_after_min: int = 60
    extensions: ExtensionsConfig = Field(default_factory=ExtensionsConfig)
    ignore: list[str] = Field(default_factory=lambda: list(DEFAULT_IGNORE))
    # Library selection from the UI: remember the last library
    # switched to (state file) across restarts, and how long the native folder-picker subprocess
    # is allowed to sit open before we give up on it.
    remember_library: bool = True
    picker_timeout_s: int = 600
    # Not part of the `web:` section itself: resolved from the same file's `categorize.nsfw.folder`
    # (the atomik-meme CLI's own NSFW auto-route folder) so the indexer can flag NSFW-category
    # items even when their sidecar's own `analysis.nsfw` is false (see `load_web_config`).
    nsfw_category_folder: str = DEFAULT_NSFW_CATEGORY_FOLDER

    def resolved_data_dir(self) -> Path:
        return self.data_dir if self.data_dir is not None else self.library / ".meme-web"


def _user_config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME / "config.yaml"
    return Path.home() / ".config" / APP_NAME / "config.yaml"


def find_config_file(explicit: Path | None) -> Path | None:
    """Return the config file that would be used, or None (built-in defaults only)."""
    if explicit is not None:
        return explicit if explicit.is_file() else None
    cwd_config = Path.cwd() / "atomik-meme.yaml"
    if cwd_config.is_file():
        return cwd_config
    user_config = _user_config_path()
    if user_config.is_file():
        return user_config
    return None


def load_web_config(
    config_path: Path | None = None,
    *,
    library: Path | None = None,
    data_dir: Path | None = None,
    host: str | None = None,
    port: int | None = None,
    open_browser: bool | None = None,
    scan_on_start: bool | None = None,
) -> WebConfig:
    """Load the `web:` section of atomik-meme.yaml, then apply CLI overrides.

    File discovery/parsing/merging is delegated to `atomik_meme.config.load_settings` -- the
    exact code the `atomik-meme` CLI itself runs -- so the `web:` and `categorize.nsfw.folder`
    keys are read from that one already-merged `Settings` object instead of re-parsing the YAML
    file a second time here.
    """
    candidate = find_config_file(config_path)
    try:
        mm_settings = load_atomik_meme_settings(candidate)
    except AtomikMemeSettingsError as exc:
        raise WebConfigError(str(exc)) from exc

    web_data = mm_settings.web or {}
    if not isinstance(web_data, dict):
        raise WebConfigError("the 'web' section of the config file must be a mapping")

    try:
        cfg = WebConfig.model_validate(web_data)
    except Exception as exc:  # noqa: BLE001 - surface as a clear config error
        raise WebConfigError(f"Invalid web config: {exc}") from exc

    if library is not None:
        cfg.library = library
    if data_dir is not None:
        cfg.data_dir = data_dir
    if host is not None:
        cfg.host = host
    if port is not None:
        cfg.port = port
    if open_browser is not None:
        cfg.open_browser = open_browser
    if scan_on_start is not None:
        cfg.scan_on_start = scan_on_start

    cfg.library = Path(cfg.library).expanduser().resolve()
    if cfg.data_dir is not None:
        cfg.data_dir = Path(cfg.data_dir).expanduser().resolve()
    if cfg.inbox_dir is not None:
        cfg.inbox_dir = Path(cfg.inbox_dir).expanduser().resolve()

    # Same already-merged `Settings` object as above: `categorize.nsfw.folder` is the CLI's own
    # NSFW auto-route folder, so the two commands always agree on which folder is "the" NSFW one.
    cfg.nsfw_category_folder = mm_settings.categorize.nsfw.folder

    return cfg
