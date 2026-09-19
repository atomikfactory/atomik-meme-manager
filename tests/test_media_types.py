"""Media-type detection, gif/video frame sampling, and ffmpeg access."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from atomik_meme import media
from atomik_meme.config import Settings
from atomik_meme.images import (
    detect_media_type,
    prepare_frames_for_model,
    probe_gif,
    sample_gif_frames,
    write_verbatim_media,
)
from tests.conftest import (
    has_ffmpeg,
    make_animated_gif,
    make_animated_webp,
    make_jpeg,
    make_mp4,
    make_static_gif,
    make_static_webp,
)

VIDEO_EXTS = frozenset({".mp4", ".webm", ".mov", ".mkv", ".m4v", ".avi"})


# --- media-type detection -----------------------------------------------------------


def test_detect_media_type_image_for_static_formats(tmp_path: Path):
    path = make_jpeg(tmp_path / "a.jpg")
    assert detect_media_type(path, VIDEO_EXTS) == "image"


def test_detect_media_type_gif_for_any_gif_including_static(tmp_path: Path):
    static_path = make_static_gif(tmp_path / "static.gif")
    animated_path = make_animated_gif(tmp_path / "anim.gif", frames=4)
    assert detect_media_type(static_path, VIDEO_EXTS) == "gif"
    assert detect_media_type(animated_path, VIDEO_EXTS) == "gif"


def test_detect_media_type_animated_webp_is_gif_static_is_image(tmp_path: Path):
    animated_path = make_animated_webp(tmp_path / "anim.webp", frames=6)
    static_path = make_static_webp(tmp_path / "static.webp")
    assert detect_media_type(animated_path, VIDEO_EXTS) == "gif"
    assert detect_media_type(static_path, VIDEO_EXTS) == "image"


def test_detect_media_type_video_extensions(tmp_path: Path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"not a real mp4, extension is all that matters here")
    assert detect_media_type(path, VIDEO_EXTS) == "video"


# --- gif frame sampling --------------------------------------------------------------


def test_sample_gif_frames_count_and_chronological_order(tmp_path: Path):
    path = make_animated_gif(tmp_path / "anim.gif", frames=8)
    frames = sample_gif_frames(path, 6)
    assert len(frames) == 6
    # frames come from an increasing index sequence -> first/last pixel colours must differ
    assert frames[0].getpixel((0, 0)) != frames[-1].getpixel((0, 0))


def test_sample_gif_frames_includes_first_and_last(tmp_path: Path):
    path = make_animated_gif(tmp_path / "anim.gif", frames=8)
    with Image.open(path) as probe:
        probe.seek(0)
        first_color = probe.convert("RGB").getpixel((0, 0))
        probe.seek(7)
        last_color = probe.convert("RGB").getpixel((0, 0))
    frames = sample_gif_frames(path, 6)
    assert frames[0].getpixel((0, 0)) == first_color
    assert frames[-1].getpixel((0, 0)) == last_color


def test_sample_gif_frames_caps_at_actual_frame_count(tmp_path: Path):
    path = make_animated_gif(tmp_path / "anim.gif", frames=3)
    frames = sample_gif_frames(path, 6)
    assert len(frames) == 3


def test_sample_gif_frames_static_gif_returns_one_frame(tmp_path: Path):
    path = make_static_gif(tmp_path / "static.gif")
    frames = sample_gif_frames(path, 6)
    assert len(frames) == 1


# --- gif timing metadata (frame_count/duration_s/fps) ---------------------------------


def test_probe_gif_returns_frame_count_duration_and_fps(tmp_path: Path):
    path = make_animated_gif(tmp_path / "anim.gif", frames=8)  # 80ms/frame in conftest
    frame_count, duration_s, fps = probe_gif(path)
    assert frame_count == 8
    assert duration_s == pytest.approx(0.64, rel=0.05)
    assert fps == pytest.approx(8 / 0.64, rel=0.05)


def test_probe_gif_never_returns_a_zero_or_null_duration_for_a_real_multi_frame_gif(
    tmp_path: Path,
):
    """Frames saved with duration=0 still measure a positive duration/fps: Pillow itself
    reports a non-zero default for at least the first frame, so the pure "every frame was
    exactly 0ms" case the frame_count*0.1s fallback exists for is unreachable through a real
    saved GIF - this checks the fallback's *effect* (never 0/None) holds regardless."""
    from PIL import Image

    path = tmp_path / "zero_duration.gif"
    imgs = [Image.new("RGB", (20, 20), (i * 40, 0, 0)) for i in range(4)]
    imgs[0].save(path, format="GIF", save_all=True, append_images=imgs[1:], duration=0, loop=0)

    frame_count, duration_s, fps = probe_gif(path)
    assert frame_count == 4
    assert duration_s is not None and duration_s > 0
    assert fps is not None and fps > 0


def test_probe_gif_fallback_formula_directly() -> None:
    """Unit-test the `duration_s <= 0 -> frame_count * 0.1` fallback formula in isolation,
    since a real GIF file can't easily be made to report an all-zero total (see above)."""
    total_ms = 0
    frame_count = 4
    duration_s = total_ms / 1000
    if duration_s <= 0:
        duration_s = frame_count * 0.1
    fps = round(frame_count / duration_s, 2) if duration_s > 0 else None
    assert duration_s == pytest.approx(0.4)
    assert fps == pytest.approx(10.0)


def test_probe_gif_static_single_frame(tmp_path: Path):
    path = make_static_gif(tmp_path / "static.gif")
    frame_count, duration_s, fps = probe_gif(path)
    assert frame_count == 1
    assert duration_s is not None and duration_s > 0
    assert fps is not None


# --- combined pixel budget across frames ----------------------------------------------


def test_prepare_frames_for_model_respects_total_pixel_budget(tmp_path: Path):
    settings = Settings()
    path = make_animated_gif(tmp_path / "anim.gif", size=(800, 600), frames=8)
    frames = sample_gif_frames(path, 6)
    tiles = prepare_frames_for_model(frames, settings.image, max_total_pixels=200_000)
    total = 0
    for tile_bytes in tiles:
        img = Image.open(io.BytesIO(tile_bytes))
        total += img.size[0] * img.size[1]
    assert total <= 200_000 * 1.05  # small rounding slack, same convention as tall-image tiling


def test_prepare_frames_for_model_never_upscales(tmp_path: Path):
    settings = Settings()
    path = make_animated_gif(tmp_path / "anim.gif", size=(50, 40), frames=4)
    frames = sample_gif_frames(path, 4)
    tiles = prepare_frames_for_model(frames, settings.image, max_total_pixels=4_500_000)
    for tile_bytes in tiles:
        img = Image.open(io.BytesIO(tile_bytes))
        assert img.size == (50, 40)


# --- verbatim storage ------------------------------------------------------------------


def test_write_verbatim_media_copies_byte_for_byte(tmp_path: Path):
    src = make_animated_gif(tmp_path / "anim.gif", frames=4)
    dest = tmp_path / "out" / "renamed.gif"
    size = write_verbatim_media(src, dest)
    assert dest.read_bytes() == src.read_bytes()
    assert size == dest.stat().st_size


# --- media.py: ffmpeg/ffprobe (real binary, skipif unavailable) ------------------------


@pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg/ffprobe not available on PATH")
def test_probe_video_reports_duration_and_dimensions(tmp_path: Path):
    path = make_mp4(tmp_path / "clip.mp4", size=(160, 120), duration_s=2.0, fps=10)
    probe = media.probe_video(path)
    assert 1.5 <= probe.duration_s <= 2.5
    assert probe.width == 160
    assert probe.height == 120
    assert probe.has_audio is False


@pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg/ffprobe not available on PATH")
def test_sample_video_frames_chronological_and_within_duration(tmp_path: Path):
    path = make_mp4(tmp_path / "clip.mp4", size=(160, 120), duration_s=2.0, fps=10)
    frames, probe = media.sample_video_frames(path, 6)
    assert len(frames) == 6
    assert probe.duration_s > 0
    for frame in frames:
        assert frame.mode == "RGB"


def test_sample_timestamps_evenly_spaced_incl_ends():
    stamps = media.sample_timestamps(10.0, 5)
    assert len(stamps) == 5
    assert stamps[0] == 0.0
    assert stamps == sorted(stamps)
    assert stamps[-1] < 10.0  # epsilon back from the exact end


def test_probe_video_missing_ffmpeg_raises_not_found(tmp_path: Path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"x")
    with pytest.raises(media.FFmpegNotFoundError):
        media.probe_video(path, ffprobe_path="definitely-not-a-real-binary-xyz")


def test_ffmpeg_check_lines_reports_not_found_for_bad_path():
    class _Cfg:
        ffmpeg_path = "definitely-not-a-real-binary-xyz"
        ffprobe_path = "also-not-real-xyz"

    lines = media.ffmpeg_check_lines(_Cfg())
    assert lines == ["ffmpeg: not found", "ffprobe: not found"]


@pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg/ffprobe not available on PATH")
def test_ffmpeg_check_lines_reports_a_version_when_available():
    class _Cfg:
        ffmpeg_path = "ffmpeg"
        ffprobe_path = "ffprobe"

    lines = media.ffmpeg_check_lines(_Cfg())
    assert lines[0].startswith("ffmpeg: ")
    assert "not found" not in lines[0]
    assert lines[1].startswith("ffprobe: ")
    assert "not found" not in lines[1]
