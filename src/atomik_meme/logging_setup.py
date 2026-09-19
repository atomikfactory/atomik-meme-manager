"""Logging configuration: Rich console handler + lazily-added rotating file handler.

Console output must degrade cleanly on Windows terminals whose default code
page cannot represent characters that show up in model output (e.g. Turkish
"İ", German umlauts): stdout/stderr are reconfigured to UTF-8 with
`errors="replace"` before anything is printed, and every file this package
writes is opened with `encoding="utf-8"`.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 5

_LEVEL_NAMES = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def parse_level(name: str, default: int = logging.INFO) -> int:
    """`logging.level` from config -> a stdlib level number, defaulting to INFO if unrecognised."""
    return _LEVEL_NAMES.get(str(name).strip().upper(), default)


def reconfigure_streams_utf8() -> None:
    """Make stdout/stderr tolerant of non-cp1252 characters on Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def setup_logging(
    verbose: bool,
    log_file: Path | None = None,
    console: Console | None = None,
    level: str = "INFO",
) -> logging.Logger:
    """Configure the root logger. Safe to call more than once (e.g. across tests).

    `level` (from `logging.level` in config) sets the root logger's level and, if `log_file`
    is given here, that file handler's level too. The console handler is independent of it:
    it always stays at WARNING, or INFO with `-v`/`--verbose`. In practice the console handler
    is also capped by whatever the root logger lets through, so setting `level` more
    restrictive than WARNING (e.g. ERROR) will also quiet the console - leave it at DEBUG,
    INFO, or WARNING for normal use and rely on `-v` for extra console detail.
    """
    reconfigure_streams_utf8()

    root = logging.getLogger()
    root.setLevel(parse_level(level))
    for handler in list(root.handlers):
        handler.close()
        root.removeHandler(handler)

    console_level = logging.INFO if verbose else logging.WARNING
    rich_handler = RichHandler(
        console=console or Console(stderr=True),
        show_time=False,
        show_path=False,
        rich_tracebacks=True,
        markup=False,
    )
    rich_handler.setLevel(console_level)
    root.addHandler(rich_handler)

    if log_file is not None:
        add_file_handler(log_file, level=parse_level(level))

    return root


def add_file_handler(path: Path, level: int = logging.INFO) -> None:
    """Attach a rotating file handler (5 x 5 MB, UTF-8) if not already attached.

    `level` is normally `logging.level` from config (parsed via `parse_level`).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == path:
            handler.setLevel(level)
            return
    handler = RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)


# httpx logs every request at INFO; that drowns the -v output of a 500-image batch.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
