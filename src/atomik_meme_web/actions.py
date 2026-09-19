"""OS-specific actions (open/reveal) and inbox filename sanitisation.

`open_path`/`reveal_path` are called as `actions.open_path(...)` (module-qualified) everywhere
so tests can `monkeypatch.setattr(actions, "open_path", ...)` and never actually launch anything.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_FORBIDDEN_CHARS = set('<>:"/\\|?*') | {chr(c) for c in range(32)}

# Windows reserves these names (case-insensitively, and regardless of any extension after the
# first dot -- "CON.txt" is just as reserved as "CON") for device files; writing to one silently
# hits the device instead of creating a file.
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


class InvalidInboxFilenameError(Exception):
    pass


def open_path(path: Path) -> None:
    """Launch `path` in the OS default application."""
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def reveal_path(path: Path) -> None:
    """Reveal `path` in the OS file browser, selected."""
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", f"/select,{path}"])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(Path(path).parent)])


def sanitize_inbox_filename(name: str) -> str:
    """Basename only, control/reserved characters stripped; refuses empty, dot-only, or a
    Windows-reserved device name (`CON`, `COM1`, ...), which is unsafe to create on Windows
    regardless of the drive/filesystem actually in use."""
    base = os.path.basename(name.replace("\\", "/")).strip()
    base = "".join(ch for ch in base if ch not in _FORBIDDEN_CHARS)
    base = base.strip(" .")
    if not base:
        raise InvalidInboxFilenameError(f"invalid inbox filename: {name!r}")
    # Windows treats everything before the *first* dot as the device name, so "CON.txt" and
    # "com1.tar.gz" are just as reserved as "CON"/"COM1".
    if base.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        raise InvalidInboxFilenameError(f"reserved filename: {name!r}")
    return base


def unique_destination(dest_dir: Path, filename: str) -> Path:
    """Atomically reserve a not-yet-existing path under `dest_dir` for `filename`.

    Never overwrites: suffixes `-1`, `-2`, ... on collision. Race-safe against concurrent
    uploads picking the same name -- `O_CREAT | O_EXCL` claims the path (creating an empty file)
    in one atomic syscall, so two requests can never both "win" the same candidate; the caller
    is expected to replace the (now claimed) file's contents immediately after.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    candidate = dest_dir / filename
    n = 0
    while True:
        try:
            fd = os.open(str(candidate), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            n += 1
            candidate = dest_dir / f"{stem}-{n}{suffix}"
            continue
        os.close(fd)
        return candidate
