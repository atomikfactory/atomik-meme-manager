"""Native folder picker: `tkinter.filedialog.askdirectory` in a separate subprocess.

Tk must not run inside the uvicorn process (it needs its own event loop and, on some platforms,
the main thread), so `POST /api/library/pick` shells out to a fresh `python -c "..."` each time
and reads back whatever path it printed. Tests monkeypatch `run_folder_picker`/`probe_native_picker`
directly and never spawn a real dialog.
"""

from __future__ import annotations

import subprocess
import sys

PROBE_TIMEOUT_S = 5.0

_PROBE_SCRIPT = "import tkinter; r = tkinter.Tk(); r.destroy()"

_PICKER_SCRIPT = """
import tkinter
from tkinter import filedialog

root = tkinter.Tk()
root.withdraw()
root.attributes("-topmost", True)
path = filedialog.askdirectory()
if path:
    print(path)
"""


class PickerUnavailableError(Exception):
    """Raised when Tk/a display isn't available to run the native picker at all."""


def probe_native_picker(timeout: float = PROBE_TIMEOUT_S) -> bool:
    """Best-effort, cached-by-the-caller check: can we even construct a Tk root here?"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", _PROBE_SCRIPT],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def run_folder_picker(timeout: float) -> str | None:
    """Run the native "choose a folder" dialog; block (in a thread pool, per the caller) until
    the user picks a folder or cancels.

    Returns the chosen absolute path, or `None` if the user cancelled. Raises
    `PickerUnavailableError` if Tk/the display isn't available, and lets a timeout propagate as
    `subprocess.TimeoutExpired` (the caller maps that to a clear error too).
    """
    result = subprocess.run(
        [sys.executable, "-c", _PICKER_SCRIPT],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise PickerUnavailableError(result.stderr.strip()[:300] or "tkinter unavailable")
    path = result.stdout.strip()
    return path or None
