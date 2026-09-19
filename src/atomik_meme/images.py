"""Image discovery, hashing, decoding/normalisation, tiling, and output writing."""

from __future__ import annotations

import hashlib
import io
import logging
import math
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence

from atomik_meme.schema import FileInfo

logger = logging.getLogger(__name__)

# Sane cap against decompression-bomb style inputs; anything larger is treated
# as a decode failure rather than silently eating gigabytes of RAM.
Image.MAX_IMAGE_PIXELS = 80_000_000


class ImageDecodeError(Exception):
    """Raised when an image file cannot be safely decoded."""


@dataclass
class LoadedImage:
    image: Image.Image  # final RGB image: EXIF-transposed, first frame, alpha flattened on white
    animated: bool
    source_format: str  # lowercase Pillow format, e.g. "jpeg", "png", "webp", "gif"
    source_width: int  # raw stored dimensions, before EXIF transpose
    source_height: int


def discover_images(root: Path, extensions: list[str], recursive: bool) -> list[Path]:
    """Find image files under `root`, sorted for deterministic ordering.

    Skips hidden files/directories (including `.meme-manager`) and anything
    whose extension is not in `extensions`.
    """
    root = Path(root)
    exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
    pattern_iter = root.rglob("*") if recursive else root.glob("*")

    results: list[Path] = []
    for candidate in pattern_iter:
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() not in exts:
            continue
        try:
            rel_parts = candidate.relative_to(root).parts
        except ValueError:
            continue
        if any(part.startswith(".") for part in rel_parts):
            continue
        results.append(candidate)

    results.sort(key=lambda p: str(p).lower())
    return results


def detect_media_type(path: Path, video_extensions: set[str] | frozenset[str]) -> str:
    """`image | gif | video` from the extension.

    A `.gif` is always `gif` (extension decides, static or not). `.webp`/`.png` are
    `gif` only when actually animated (Pillow `is_animated`); otherwise `image`.
    Anything in `video_extensions` is `video`.
    """
    ext = path.suffix.lower()
    if ext in video_extensions:
        return "video"
    if ext == ".gif":
        return "gif"
    if ext in (".webp", ".png"):
        try:
            with Image.open(path) as img:
                if getattr(img, "is_animated", False):
                    return "gif"
        except Exception:  # noqa: BLE001 - let the real decode step raise a proper error later
            pass
    return "image"


def _evenly_spaced_indices(total: int, n: int) -> list[int]:
    """`n` indices into `range(total)`, evenly spaced, always including 0 and `total - 1`."""
    if total <= 1:
        return [0]
    n = max(1, min(n, total))
    if n == 1:
        return [0]
    return sorted({round(i * (total - 1) / (n - 1)) for i in range(n)})


def sample_gif_frames(path: Path, n_frames: int) -> list[Image.Image]:
    """Evenly spaced frames (incl. first and last) from an animated GIF/WebP/APNG.

    Uses Pillow's `ImageSequence.Iterator` so multi-frame formats that rely on
    per-frame disposal (partial updates) are composited correctly rather than
    yielding raw, un-composited deltas.
    """
    with Image.open(path) as img:
        all_frames = [frame.convert("RGBA").copy() for frame in ImageSequence.Iterator(img)]

    indices = _evenly_spaced_indices(len(all_frames), n_frames)
    result: list[Image.Image] = []
    for idx in indices:
        frame = all_frames[idx]
        if frame.mode in ("RGBA", "LA"):
            background = Image.new("RGB", frame.size, (255, 255, 255))
            background.paste(frame, mask=frame.split()[-1])
            result.append(background)
        else:
            result.append(frame.convert("RGB"))
    return result


def probe_gif(path: Path) -> tuple[int, float | None, float | None]:
    """`(frame_count, duration_s, fps)` for a gif/animated-webp/APNG's full timeline.

    `duration_s` sums each frame's `info["duration"]` (ms, defaulting to 100ms for a frame
    that doesn't report one) and falls back to `frame_count * 0.1` when that sum is zero
    (e.g. every frame explicitly reports a 0ms duration). `fps` is `frame_count / duration_s`,
    rounded to 2 decimals; both are `None` only if `duration_s` works out to 0 (frame_count 0).
    """
    frame_count = 0
    total_ms = 0
    with Image.open(path) as img:
        for frame in ImageSequence.Iterator(img):
            frame_count += 1
            total_ms += frame.info.get("duration", 100) or 0

    duration_s = total_ms / 1000
    if duration_s <= 0:
        duration_s = frame_count * 0.1

    if duration_s <= 0:
        return frame_count, None, None
    return frame_count, duration_s, round(frame_count / duration_s, 2)


def prepare_frames_for_model(
    frames: list[Image.Image], image_cfg, max_total_pixels: int
) -> list[bytes]:
    """JPEG-encode `frames` (chronological order) for the vision model.

    Each frame is resized to fit `model_max_pixels`/`model_max_side` individually,
    then the whole set is scaled down together (never distorted per-frame) if their
    combined pixel count still exceeds `max_total_pixels` (`media.frame_max_total_pixels`) -
    the same combined-budget idea as tall-image tiling, applied to sampled frames.
    """
    resized = [
        _resize_for_model(f, image_cfg.model_max_pixels, image_cfg.model_max_side) for f in frames
    ]
    total_pixels = sum(w * h for w, h in (im.size for im in resized))
    if max_total_pixels and total_pixels > max_total_pixels:
        scale = math.sqrt(max_total_pixels / total_pixels)
        resized = [
            im.resize(
                (max(1, round(im.size[0] * scale)), max(1, round(im.size[1] * scale))),
                Image.LANCZOS,
            )
            for im in resized
        ]
    return [_encode_jpeg(im, image_cfg.model_jpeg_quality) for im in resized]


def write_verbatim_media(src_path: Path, dest_path: Path) -> int:
    """Copy `src_path` to `dest_path` byte-for-byte (atomic tmp + `os.replace`).

    Used for gif/video, which are always stored verbatim under their original
    (lowercased) extension - streamed via `shutil.copyfile`, never re-encoded.
    Returns the destination file size in bytes.
    """
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_name(dest_path.name + ".tmp")
    try:
        shutil.copyfile(src_path, tmp_path)
        os.replace(tmp_path, dest_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
    return dest_path.stat().st_size


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def load_image(path: Path) -> LoadedImage:
    """Decode an image file: verify, reopen, EXIF-transpose, flatten to RGB.

    Animated images keep only frame 0 (`animated=True` is still recorded).
    Raises `ImageDecodeError` on any failure (corrupt file, unsupported
    format, decompression bomb, etc.).
    """
    try:
        with Image.open(path) as probe:
            probe.verify()
    except Exception as exc:  # noqa: BLE001 - any decode issue becomes ImageDecodeError
        raise ImageDecodeError(f"{type(exc).__name__}: {exc}") from exc

    try:
        with Image.open(path) as img:
            source_format = (img.format or "").lower()
            animated = bool(getattr(img, "is_animated", False))
            if animated:
                img.seek(0)
            source_width, source_height = img.size
            transposed = ImageOps.exif_transpose(img)
            transposed.load()
            if transposed.mode in ("RGBA", "LA") or (
                transposed.mode == "P" and "transparency" in transposed.info
            ):
                rgba = transposed.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.split()[-1])
                final = background
            else:
                final = transposed.convert("RGB")
            final.load()
    except Exception as exc:  # noqa: BLE001
        raise ImageDecodeError(f"{type(exc).__name__}: {exc}") from exc

    return LoadedImage(
        image=final,
        animated=animated,
        source_format=source_format,
        source_width=source_width,
        source_height=source_height,
    )


_UNLIMITED_SIDE = 10**9


def _resize_for_model(img: Image.Image, max_pixels: int, max_side: int) -> Image.Image:
    """Downscale (never upscale) so `w*h <= max_pixels` and `max(w,h) <= max_side`."""
    w, h = img.size
    scale = 1.0
    if w * h > max_pixels:
        scale = min(scale, math.sqrt(max_pixels / (w * h)))
    if max(w, h) * scale > max_side:
        scale = min(scale, max_side / max(w, h))
    if scale < 1.0:
        new_w = max(1, round(w * scale))
        new_h = max(1, round(h * scale))
        img = img.resize((new_w, new_h), Image.LANCZOS)
    return img


def _encode_jpeg(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _tile_count(long_side: int, short_side: int, max_pixels: int, max_tiles: int) -> int:
    target = max(1, max_pixels // max(1, short_side))
    n = math.ceil(long_side / target)
    return max(1, min(n, max_tiles))


def _cut_tiles(
    img: Image.Image, n_tiles: int, overlap_px: int, vertical: bool
) -> list[Image.Image]:
    w, h = img.size
    long_side = h if vertical else w
    base = math.ceil(long_side / n_tiles)
    tiles = []
    for i in range(n_tiles):
        start = max(0, i * base - (overlap_px if i > 0 else 0))
        end = min(long_side, (i + 1) * base + (overlap_px if i < n_tiles - 1 else 0))
        box = (0, start, w, end) if vertical else (start, 0, end, h)
        tiles.append(img.crop(box))
    return tiles


def prepare_for_model(img: Image.Image, image_cfg) -> list[bytes]:
    """Produce 1..max_tiles JPEG-encoded tiles of `img` sized for the vision model.

    Tall/wide comics are cut along their long axis into overlapping strips;
    everything else is resized to fit `model_max_pixels` /
    `model_max_side` as a single tile. When tiling, the combined pixel count
    of all tiles sent in one request is additionally capped at
    `tall_image.max_total_pixels` (a large image sends many prompt tokens;
    without this a 4-tile comic can blow past `num_ctx`).
    """
    w, h = img.size
    tall_cfg = image_cfg.tall_image
    long_side = max(w, h)
    short_side = max(1, min(w, h))
    aspect = long_side / short_side

    if tall_cfg.enabled and aspect >= tall_cfg.min_aspect and long_side >= tall_cfg.min_long_side:
        vertical = h >= w
        n_tiles = _tile_count(long_side, short_side, image_cfg.model_max_pixels, tall_cfg.max_tiles)
        if n_tiles > 1:
            tiles = _cut_tiles(img, n_tiles, tall_cfg.overlap_px, vertical)
            # Tiles deliberately ignore `model_max_side`: a strip's long side is not what
            # limits readability, its width is. Only the pixel budgets apply, so a 544 px
            # wide comic keeps ~90% of its native width instead of being squeezed to 355 px.
            resized_tiles = [
                _resize_for_model(t, image_cfg.model_max_pixels, _UNLIMITED_SIDE) for t in tiles
            ]
            total_pixels = sum(t.size[0] * t.size[1] for t in resized_tiles)
            max_total = getattr(tall_cfg, "max_total_pixels", None)
            if max_total and total_pixels > max_total:
                scale = math.sqrt(max_total / total_pixels)
                resized_tiles = [
                    t.resize(
                        (max(1, round(t.size[0] * scale)), max(1, round(t.size[1] * scale))),
                        Image.LANCZOS,
                    )
                    for t in resized_tiles
                ]
                total_pixels = sum(t.size[0] * t.size[1] for t in resized_tiles)
            logger.debug("tiled image into %d tile(s), total_pixels=%d", n_tiles, total_pixels)
            return [_encode_jpeg(t, image_cfg.model_jpeg_quality) for t in resized_tiles]

    resized = _resize_for_model(img, image_cfg.model_max_pixels, image_cfg.model_max_side)
    logger.debug("single tile, total_pixels=%d", resized.size[0] * resized.size[1])
    return [_encode_jpeg(resized, image_cfg.model_jpeg_quality)]


def should_copy_verbatim(loaded: LoadedImage, image_cfg) -> bool:
    """True when the source is a JPEG we can copy byte-for-byte instead of re-encoding."""
    return loaded.source_format in ("jpeg", "jpg") and image_cfg.copy_jpeg_verbatim


def write_output_image(src_path: Path, loaded: LoadedImage, dest_path: Path, image_cfg) -> FileInfo:
    """Write the final output JPEG (atomic) and return its `FileInfo`.

    JPEG sources are copied verbatim when `copy_jpeg_verbatim` is set (no
    quality loss, no re-encode); everything else is saved as JPEG at
    `output_jpeg_quality`, preserving the original (post-EXIF-transpose)
    resolution.
    """
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_name(dest_path.name + ".tmp")

    try:
        if should_copy_verbatim(loaded, image_cfg):
            shutil.copyfile(src_path, tmp_path)
            width, height = loaded.source_width, loaded.source_height
        else:
            loaded.image.save(
                tmp_path, format="JPEG", quality=image_cfg.output_jpeg_quality, optimize=True
            )
            width, height = loaded.image.size
        os.replace(tmp_path, dest_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    size_bytes = dest_path.stat().st_size

    return FileInfo(
        name=dest_path.name,
        width=width,
        height=height,
        bytes=size_bytes,
        format="jpeg",
        animated=loaded.animated,
    )
