"""In-app folder browser: `GET /api/fs/list`. Directories only, never files."""

from __future__ import annotations

import ctypes
import logging
import os
import string
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_ENTRIES = 500
HAS_CHILDREN_SCAN_LIMIT = 200

_HIDDEN_NAMES = {"$recycle.bin", "system volume information"}
_FILE_ATTRIBUTE_HIDDEN = 0x2
_FILE_ATTRIBUTE_SYSTEM = 0x4


class FsListError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def list_drives() -> list[dict[str, str]]:
    """Windows drive letters (`C:\\`, `D:\\`, ...), or a single `/` root elsewhere."""
    if not sys.platform.startswith("win"):
        return [{"name": "/", "path": "/"}]
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()  # type: ignore[attr-defined]
        drives = [
            f"{letter}:\\" for i, letter in enumerate(string.ascii_uppercase) if bitmask & (1 << i)
        ]
    except (AttributeError, OSError):
        drives = [
            f"{letter}:\\" for letter in string.ascii_uppercase if os.path.exists(f"{letter}:\\")
        ]
    return [{"name": d, "path": d} for d in drives]


def _is_hidden(entry: os.DirEntry) -> bool:
    # `.`-prefixed is skipped on every platform -- Windows doesn't treat it as a real
    # hidden marker itself, but a `.git`/`.meme-web`-style folder should still never show up in
    # the picker there either. The Windows hidden/system *attribute* check is additional, not a
    # replacement for this.
    if entry.name.startswith("."):
        return True
    if entry.name.lower() in _HIDDEN_NAMES:
        return True
    if not sys.platform.startswith("win"):
        return False
    try:
        attrs = entry.stat(follow_symlinks=False).st_file_attributes  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        return False
    return bool(attrs & (_FILE_ATTRIBUTE_HIDDEN | _FILE_ATTRIBUTE_SYSTEM))


def _has_children(path: Path, limit: int = HAS_CHILDREN_SCAN_LIMIT) -> bool:
    """Cheap, bounded check: is there at least one non-hidden subdirectory?

    Stops after `limit` entries (assuming "yes" if the cap is hit without an answer) so a huge
    flat directory of files doesn't make every row of a listing pay for a full scan.
    """
    try:
        with os.scandir(path) as it:
            for i, entry in enumerate(it):
                if i >= limit:
                    return True
                try:
                    if entry.is_dir(follow_symlinks=False) and not _is_hidden(entry):
                        return True
                except OSError:
                    continue
    except OSError:
        return False
    return False


def list_directory(path: str | None) -> dict:
    """`{path, parent, entries:[{name, path, has_children}], roots:[{name, path}]}`.

    `path=None` returns only `roots` (path/parent are `None`, entries empty) -- the browser's
    initial "pick a drive" screen.
    """
    roots = list_drives()
    if path is None:
        return {"path": None, "parent": None, "entries": [], "roots": roots}

    p = Path(path)
    if not p.is_absolute():
        raise FsListError(400, "path must be absolute")
    if not p.exists():
        raise FsListError(404, "path not found")
    if not p.is_dir():
        raise FsListError(404, "path is not a directory")

    names: list[str] = []
    try:
        with os.scandir(p) as it:
            for entry in it:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                if _is_hidden(entry):
                    continue
                names.append(entry.name)
    except PermissionError as exc:
        raise FsListError(403, "permission denied") from exc
    except OSError as exc:
        raise FsListError(404, str(exc)) from exc

    names.sort(key=str.lower)
    truncated = len(names) > MAX_ENTRIES
    names = names[:MAX_ENTRIES]

    entries = [
        {"name": name, "path": str(p / name), "has_children": _has_children(p / name)}
        for name in names
    ]
    parent = str(p.parent) if p.parent != p else None
    result: dict = {"path": str(p), "parent": parent, "entries": entries, "roots": roots}
    if truncated:
        result["truncated"] = True
    return result
