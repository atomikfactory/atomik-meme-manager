"""Persisted "last library"/"recent libraries" state and startup precedence.

State file: `%LOCALAPPDATA%\\meme-web\\state.json` on Windows, `$XDG_STATE_HOME/meme-web/state.json`
or `~/.local/state/meme-web/state.json` elsewhere. Reading the path through `LOCALAPPDATA`/
`XDG_STATE_HOME` (rather than hardcoding it) is what lets tests redirect it to a tmp dir via
`monkeypatch.setenv` -- exactly like `atomik_meme.config`'s own `APPDATA` handling -- so a test
run never reads or writes the real one.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from atomik_meme_web.config import WebConfig

logger = logging.getLogger(__name__)

APP_DIR_NAME = "meme-web"
MAX_RECENT = 10


class RecentLibraryEntry(BaseModel):
    path: str
    last_opened: str


class LibraryState(BaseModel):
    version: int = 1
    last_library: str | None = None
    recent: list[RecentLibraryEntry] = Field(default_factory=list)


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def state_file_path() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
    else:
        base = os.environ.get("XDG_STATE_HOME")
        root = Path(base) if base else Path.home() / ".local" / "state"
    return root / APP_DIR_NAME / "state.json"


def load_state() -> LibraryState:
    path = state_file_path()
    if not path.is_file():
        return LibraryState()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return LibraryState.model_validate(data)
    except Exception:  # noqa: BLE001 - a corrupt state file must never crash startup
        logger.warning("failed to read library state file %s; ignoring it", path)
        return LibraryState()


def save_state(state: LibraryState) -> None:
    path = state_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state.model_dump(mode="json"), indent=2), encoding="utf-8")
    os.replace(tmp, path)


def record_library_opened(library_path: Path) -> LibraryState:
    """Set `last_library` and push `library_path` to the front of `recent` (deduped, capped)."""
    state = load_state()
    path_str = str(library_path)
    now = _utcnow_iso()
    state.recent = [r for r in state.recent if r.path != path_str]
    state.recent.insert(0, RecentLibraryEntry(path=path_str, last_opened=now))
    state.recent = state.recent[:MAX_RECENT]
    state.last_library = path_str
    save_state(state)
    return state


def remove_recent(path: str) -> LibraryState:
    state = load_state()
    state.recent = [r for r in state.recent if r.path != path]
    save_state(state)
    return state


def resolve_startup_library(config: WebConfig, cli_library: Path | None) -> Path | None:
    """First match wins: `--library` flag > remembered `last_library` (if
    `remember_library` and the folder still exists) > `web.library` from the config (itself
    already defaulted to `./output`) if it exists > no library.
    """
    if cli_library is not None:
        return Path(cli_library).expanduser().resolve()
    if config.remember_library:
        state = load_state()
        if state.last_library:
            candidate = Path(state.last_library)
            if candidate.is_dir():
                return candidate
    if config.library is not None and Path(config.library).is_dir():
        return Path(config.library)
    return None
