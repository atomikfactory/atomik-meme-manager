"""Thumbnail + dHash generation and cache.

Thumbnails are WebP, generated lazily on first request, cached at
`thumbs/{sha[:2]}/{sha}_{w}.webp` under the data dir, written atomically (`.tmp` + `os.replace`).
Only the configured `thumb_sizes` ever exist on disk: an arbitrary requested width is snapped to
the nearest configured size before anything is decoded, so a hostile page spamming
`?w=1`, `?w=2`, `?w=3`, ... can't force unbounded distinct thumbnail generations. The source frame
(a real image decode, or one `ffmpeg` extraction for video) is decoded exactly once per item and
used to produce every configured size in the same pass, under one per-item lock. A repeatedly
failing item (corrupt file, missing codec, ...) gets a failure sentinel with a cooldown so it
isn't retried on every request/prewarm pass.
"""

from __future__ import annotations

import io
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path

from PIL import Image, ImageOps

from atomik_meme.media import FFmpegNotFoundError, VideoDecodeError, extract_frame
from atomik_meme_web.db import Database

logger = logging.getLogger(__name__)

THUMB_QUALITY = 82
_PLACEHOLDER_COLOR = (60, 60, 68)
DEFAULT_RETRY_AFTER_MIN = 60


def compute_dhash(image: Image.Image, hash_size: int = 8) -> str:
    """8x8 difference hash -> 16 lowercase hex chars (64 bits).

    Uses `.load()` (a `PixelAccess`) rather than `.getdata()`, which Pillow has deprecated.
    """
    gray = image.convert("L").resize((hash_size + 1, hash_size), Image.LANCZOS)
    pixels = gray.load()
    value = 0
    for row in range(hash_size):
        for col in range(hash_size):
            value <<= 1
            if pixels[col, row] > pixels[col + 1, row]:
                value |= 1
    return f"{value:0{hash_size * hash_size // 4}x}"


def _resize_to_width(frame: Image.Image, target_w: int) -> Image.Image:
    w, h = frame.size
    if w <= 0 or h <= 0:
        return frame
    scale = target_w / w
    new_h = max(1, round(h * scale))
    return frame.resize((max(1, target_w), new_h), Image.LANCZOS)


def _load_source_frame(item_row: sqlite3.Row) -> Image.Image:
    path = Path(item_row["path"])
    media_type = item_row["media_type"]
    if media_type == "video":
        duration = item_row["duration_s"] or 0.0
        timestamp = min(1.0, duration * 0.1) if duration else 0.0
        raw = extract_frame(path, timestamp)
        return Image.open(io.BytesIO(raw)).convert("RGB")
    if media_type == "gif":
        with Image.open(path) as img:
            img.seek(0)
            transposed = ImageOps.exif_transpose(img)
            return transposed.convert("RGB")
    with Image.open(path) as img:
        transposed = ImageOps.exif_transpose(img)
        return transposed.convert("RGB")


class ThumbnailCache:
    def __init__(
        self,
        data_dir: Path,
        db: Database,
        sizes: list[int] | None = None,
        retry_after_min: int = DEFAULT_RETRY_AFTER_MIN,
    ):
        self.dir = Path(data_dir) / "thumbs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db = db
        self.sizes: list[int] = sorted({int(s) for s in sizes}) if sizes else [320]
        self.retry_after_s = max(0, retry_after_min) * 60
        self._locks_guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._placeholder_cache: dict[int, Path] = {}
        self._threads_guard = threading.Lock()
        self._threads: list[threading.Thread] = []

    def snap_size(self, w: int) -> int:
        """The configured size nearest to `w` -- only these files can ever exist on disk."""
        return min(self.sizes, key=lambda s: abs(s - w))

    def path_for(self, sha256: str, w: int) -> Path:
        """Path for an *already-snapped* size (see `snap_size`)."""
        return self.dir / sha256[:2] / f"{sha256}_{w}.webp"

    def _failure_path(self, sha256: str) -> Path:
        return self.dir / sha256[:2] / f"{sha256}.failed"

    def _recently_failed(self, sha256: str) -> bool:
        if self.retry_after_s <= 0:
            return False
        fpath = self._failure_path(sha256)
        try:
            failed_at = float(fpath.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return False
        return (time.time() - failed_at) < self.retry_after_s

    def _record_failure(self, sha256: str) -> None:
        fpath = self._failure_path(sha256)
        fpath.parent.mkdir(parents=True, exist_ok=True)
        tmp = fpath.with_name(fpath.name + ".tmp")
        tmp.write_text(str(time.time()), encoding="utf-8")
        os.replace(tmp, fpath)

    def _clear_failure(self, sha256: str) -> None:
        try:
            self._failure_path(sha256).unlink()
        except FileNotFoundError:
            pass

    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def get_or_create(self, item_row: sqlite3.Row, w: int) -> Path:
        sha = item_row["sha256"]
        snapped = self.snap_size(w)
        dest = self.path_for(sha, snapped)
        if dest.is_file():
            return dest
        lock = self._lock_for(sha)
        with lock:
            if dest.is_file():
                return dest
            if self._recently_failed(sha):
                return self._placeholder(snapped)
            try:
                self._generate_all_sizes(item_row)
                self._clear_failure(sha)
            except (FFmpegNotFoundError, VideoDecodeError, OSError) as exc:
                logger.warning("thumbnail generation failed for %s: %s", item_row["path"], exc)
                self._record_failure(sha)
                return self._placeholder(snapped)
            except Exception:
                logger.exception("thumbnail generation failed for %s", item_row["path"])
                self._record_failure(sha)
                return self._placeholder(snapped)
        return dest if dest.is_file() else self._placeholder(snapped)

    def _generate_all_sizes(self, item_row: sqlite3.Row) -> None:
        """Decode the source frame once and write every configured size from it."""
        sha = item_row["sha256"]
        missing_sizes = [w for w in self.sizes if not self.path_for(sha, w).is_file()]
        if not missing_sizes:
            return
        frame = _load_source_frame(item_row)

        dhash = compute_dhash(frame)
        if item_row["dhash"] != dhash:
            with self.db.write() as conn:
                conn.execute("UPDATE items SET dhash=? WHERE sha256=?", (dhash, sha))

        for w in missing_sizes:
            dest = self.path_for(sha, w)
            thumb = _resize_to_width(frame, w)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + f".{w}.tmp")
            thumb.save(tmp, format="WEBP", quality=THUMB_QUALITY, method=4)
            os.replace(tmp, dest)

    def _placeholder(self, w: int) -> Path:
        cached = self._placeholder_cache.get(w)
        if cached is not None and cached.is_file():
            return cached
        dest = self.dir / f"_placeholder_{w}.webp"
        if not dest.is_file():
            img = Image.new("RGB", (max(1, w), max(1, round(w * 0.75))), _PLACEHOLDER_COLOR)
            tmp = dest.with_name(dest.name + ".tmp")
            img.save(tmp, format="WEBP", quality=THUMB_QUALITY)
            os.replace(tmp, dest)
        self._placeholder_cache[w] = dest
        return dest

    def prewarm(self, limit: int = 500) -> None:
        """Best-effort background warm-up of the most recently indexed items."""
        rows = self.db.conn.execute(
            "SELECT * FROM items ORDER BY indexed_at DESC LIMIT ?", (limit,)
        ).fetchall()
        for row in rows:
            if all(self.path_for(row["sha256"], w).is_file() for w in self.sizes):
                continue
            try:
                self.get_or_create(row, self.sizes[0])
            except Exception:  # noqa: BLE001 - best-effort warm-up, never fatal
                logger.exception("prewarm failed for %s", row["path"])

    def prewarm_async(self, limit: int = 500) -> None:
        thread = threading.Thread(
            target=self.prewarm, args=(limit,), daemon=True, name="atomik-meme-web-prewarm"
        )
        with self._threads_guard:
            self._threads = [t for t in self._threads if t.is_alive()]
            self._threads.append(thread)
        thread.start()

    def join(self, timeout: float | None = None) -> None:
        """Wait for any in-flight prewarm threads to finish (used at shutdown)."""
        with self._threads_guard:
            threads = list(self._threads)
        for thread in threads:
            thread.join(timeout=timeout)
