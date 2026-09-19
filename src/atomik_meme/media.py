"""ffmpeg/ffprobe access: video probing, frame extraction, and tool version checks.

Kept in one small module, separate from the pure-Pillow code in `images.py`, so
tests can monkeypatch the function boundary (`sample_video_frames`) instead of
shelling out to a real ffmpeg - and so `check` can report ffmpeg/ffprobe
availability independently of everything else.
"""

from __future__ import annotations

import io
import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)


class FFmpegNotFoundError(Exception):
    """Raised when the configured ffmpeg/ffprobe executable cannot be run at all."""


class VideoDecodeError(Exception):
    """Raised when ffprobe/ffmpeg runs but fails to probe or extract frames from a video."""


@dataclass
class VideoProbe:
    duration_s: float
    fps: float | None
    frame_count: int | None
    width: int
    height: int
    has_audio: bool


def probe_video(path: Path, ffprobe_path: str = "ffprobe", timeout: float = 30) -> VideoProbe:
    """Run ffprobe and return duration/fps/frame_count/width/height/has_audio."""
    cmd = [
        ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,width,height,r_frame_rate,nb_frames",
        "-of",
        "json",
        str(path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise FFmpegNotFoundError(f"ffprobe executable not found: {ffprobe_path}") from exc
    except subprocess.SubprocessError as exc:
        raise VideoDecodeError(f"ffprobe failed on {path}: {exc}") from exc

    if result.returncode != 0:
        raise VideoDecodeError(f"ffprobe failed on {path}: {result.stderr.strip()[:300]}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VideoDecodeError(f"ffprobe returned invalid JSON for {path}: {exc}") from exc

    fmt = data.get("format", {}) or {}
    try:
        duration = float(fmt.get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0

    streams = data.get("streams", []) or []
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    width = height = 0
    fps: float | None = None
    frame_count: int | None = None
    if video_stream:
        try:
            width = int(video_stream.get("width") or 0)
            height = int(video_stream.get("height") or 0)
        except (TypeError, ValueError):
            width = height = 0
        rate = video_stream.get("r_frame_rate")
        if rate and "/" in str(rate):
            num, _, den = str(rate).partition("/")
            try:
                den_f = float(den)
                fps = (float(num) / den_f) if den_f else None
            except ValueError:
                fps = None
        nb_frames = video_stream.get("nb_frames")
        if nb_frames not in (None, "N/A"):
            try:
                frame_count = int(nb_frames)
            except (TypeError, ValueError):
                frame_count = None

    if duration <= 0:
        raise VideoDecodeError(f"Could not determine duration for {path}")

    return VideoProbe(
        duration_s=duration,
        fps=fps,
        frame_count=frame_count,
        width=width,
        height=height,
        has_audio=has_audio,
    )


def extract_frame(
    path: Path, timestamp_s: float, ffmpeg_path: str = "ffmpeg", timeout: float = 30
) -> bytes:
    """Extract one frame at `timestamp_s` as raw MJPEG bytes via an image2pipe."""
    cmd = [
        ffmpeg_path,
        "-ss",
        f"{max(0.0, timestamp_s):.3f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise FFmpegNotFoundError(f"ffmpeg executable not found: {ffmpeg_path}") from exc
    except subprocess.SubprocessError as exc:
        raise VideoDecodeError(f"ffmpeg failed on {path} at {timestamp_s:.2f}s: {exc}") from exc

    if result.returncode != 0 or not result.stdout:
        stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        raise VideoDecodeError(
            f"ffmpeg produced no frame for {path} at {timestamp_s:.2f}s: {stderr[:300]}"
        )
    return result.stdout


def sample_timestamps(duration_s: float, n_frames: int) -> list[float]:
    """Evenly spaced timestamps in chronological order, including ~0 and ~end."""
    n_frames = max(1, n_frames)
    if n_frames == 1:
        return [round(duration_s / 2, 3)]
    # An epsilon back from the very end avoids asking ffmpeg to seek past the last decodable
    # frame (observed: input-seeking with `-ss` before `-i` can produce zero frames within a
    # few hundred ms of a short clip's reported duration - container/frame-rate rounding at
    # the tail of the file).
    end = max(0.0, duration_s - 0.2)
    step = end / (n_frames - 1)
    return [round(min(end, i * step), 3) for i in range(n_frames)]


def sample_video_frames(
    path: Path,
    n_frames: int,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
) -> tuple[list[Image.Image], VideoProbe]:
    """Probe `path` then extract up to `n_frames` evenly spaced frames, chronological order.

    This is the function boundary processor tests monkeypatch to avoid shelling
    out to a real ffmpeg.
    """
    probe = probe_video(path, ffprobe_path)
    timestamps = sample_timestamps(probe.duration_s, n_frames)
    frames: list[Image.Image] = []
    for ts in timestamps:
        raw = extract_frame(path, ts, ffmpeg_path)
        img = Image.open(io.BytesIO(raw))
        img.load()
        frames.append(img.convert("RGB"))
    return frames, probe


_VERSION_RE = re.compile(r"version\s+(\S+)")


def probe_tool_version(path: str, timeout: float = 5) -> str | None:
    """Return what `<path> -version` reports, or None if the tool cannot be run at all."""
    try:
        result = subprocess.run(
            [path, "-version"], capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    first_line = (result.stdout or "").splitlines()[0] if result.stdout else ""
    match = _VERSION_RE.search(first_line)
    return match.group(1) if match else (first_line.strip() or None)


def ffmpeg_check_lines(media_cfg) -> list[str]:
    """`["ffmpeg: <version>", "ffprobe: <version>"]` (or "not found") for the `check` command."""
    lines = []
    for label, path in (("ffmpeg", media_cfg.ffmpeg_path), ("ffprobe", media_cfg.ffprobe_path)):
        version = probe_tool_version(path)
        lines.append(f"{label}: {version or 'not found'}")
    return lines
