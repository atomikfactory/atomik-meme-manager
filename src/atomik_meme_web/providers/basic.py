"""`basic` provider: media_type, dimensions, duration/fps, filesystem timestamps.

Reuses `atomik_meme.images`/`atomik_meme.media` so gif/video probing behaves identically to
the CLI (same animated-webp/png detection, same ffprobe JSON parsing).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from atomik_meme.images import detect_media_type, probe_gif
from atomik_meme.media import FFmpegNotFoundError, VideoDecodeError, probe_video
from atomik_meme_web.config import WebConfig

logger = logging.getLogger(__name__)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class BasicProvider:
    name = "basic"

    def __init__(self, config: WebConfig):
        self.video_exts = frozenset(e.lower() for e in config.extensions.video)

    def applies(self, path: Path, fields: dict[str, Any]) -> bool:
        return True

    def extract(
        self, path: Path, rel_path: str, library_root: Path, fields: dict[str, Any]
    ) -> dict[str, Any]:
        st = path.stat()
        media_type = detect_media_type(path, self.video_exts)
        out: dict[str, Any] = {
            "media_type": media_type,
            "size_bytes": st.st_size,
            "mtime_ns": st.st_mtime_ns,
            "created_at": _iso(st.st_ctime),
            "modified_at": _iso(st.st_mtime),
            "width": None,
            "height": None,
            "duration_s": None,
            "fps": None,
            "frame_count": None,
        }
        try:
            if media_type == "video":
                probe = probe_video(path)
                out["width"] = probe.width or None
                out["height"] = probe.height or None
                out["duration_s"] = probe.duration_s
                out["fps"] = probe.fps
                out["frame_count"] = probe.frame_count
            elif media_type == "gif":
                with Image.open(path) as img:
                    out["width"], out["height"] = img.size
                frame_count, duration_s, fps = probe_gif(path)
                out["frame_count"] = frame_count
                out["duration_s"] = duration_s
                out["fps"] = fps
            else:
                with Image.open(path) as img:
                    out["width"], out["height"] = img.size
        except (FFmpegNotFoundError, VideoDecodeError) as exc:
            logger.warning("basic provider: failed to probe video %s: %s", path, exc)
        except Exception as exc:  # noqa: BLE001 - never fail the scan over one bad file's probe
            logger.warning("basic provider: failed to probe %s: %s", path, exc)
        return out
