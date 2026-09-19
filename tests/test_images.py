import io
from pathlib import Path

import pytest
from PIL import Image

from atomik_meme.config import Settings
from atomik_meme.images import (
    ImageDecodeError,
    discover_images,
    load_image,
    prepare_for_model,
    sha256_file,
    should_copy_verbatim,
    write_output_image,
)
from tests.conftest import (
    make_animated_gif,
    make_corrupted_jpg,
    make_jpeg,
    make_rgba_png,
    make_tall_comic,
    make_webp,
)


def test_discover_images_sorted_and_filtered(tmp_path: Path):
    root = tmp_path
    make_jpeg(root / "b.jpg")
    make_jpeg(root / "a.jpg")
    (root / "notes.txt").write_text("hi", encoding="utf-8")
    hidden_dir = root / ".meme-manager"
    hidden_dir.mkdir()
    make_jpeg(hidden_dir / "should_be_skipped.jpg")

    found = discover_images(root, [".jpg", ".jpeg"], recursive=True)
    names = [p.name for p in found]
    assert names == ["a.jpg", "b.jpg"]


def test_discover_images_recursive_flag(tmp_path: Path):
    root = tmp_path
    make_jpeg(root / "top.jpg")
    sub = root / "sub"
    sub.mkdir()
    make_jpeg(sub / "nested.jpg")

    non_recursive = discover_images(root, [".jpg"], recursive=False)
    assert [p.name for p in non_recursive] == ["top.jpg"]

    recursive = discover_images(root, [".jpg"], recursive=True)
    assert {p.name for p in recursive} == {"top.jpg", "nested.jpg"}


def test_sha256_file_matches_hashlib(tmp_path: Path):
    import hashlib

    path = tmp_path / "f.bin"
    path.write_bytes(b"hello world" * 1000)
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert sha256_file(path) == expected


def test_load_image_flattens_alpha_on_white(tmp_path: Path):
    path = make_rgba_png(tmp_path / "alpha.png", size=(10, 10), color=(255, 0, 0, 0))
    loaded = load_image(path)
    assert loaded.image.mode == "RGB"
    # fully transparent red pixel should be flattened to white
    assert loaded.image.getpixel((0, 0)) == (255, 255, 255)
    assert loaded.animated is False
    assert loaded.source_format == "png"


def test_load_image_animated_gif_keeps_frame_zero(tmp_path: Path):
    path = make_animated_gif(tmp_path / "anim.gif")
    loaded = load_image(path)
    assert loaded.animated is True
    assert loaded.image.mode == "RGB"


def test_load_image_webp(tmp_path: Path):
    path = make_webp(tmp_path / "pic.webp")
    loaded = load_image(path)
    assert loaded.source_format == "webp"
    assert loaded.image.mode == "RGB"


def test_load_image_corrupted_raises(tmp_path: Path):
    path = make_corrupted_jpg(tmp_path / "broken.jpg")
    with pytest.raises(ImageDecodeError):
        load_image(path)


def test_prepare_for_model_single_tile_for_normal_image(tmp_path: Path):
    settings = Settings()
    path = make_jpeg(tmp_path / "normal.jpg", size=(460, 795))
    loaded = load_image(path)
    tiles = prepare_for_model(loaded.image, settings.image)
    assert len(tiles) == 1
    img = Image.open(io.BytesIO(tiles[0]))
    assert img.format == "JPEG"


def test_prepare_for_model_tiles_tall_comic(tmp_path: Path):
    settings = Settings()
    path = make_tall_comic(tmp_path / "comic.jpg", size=(400, 4000))
    loaded = load_image(path)
    tiles = prepare_for_model(loaded.image, settings.image)
    # 400x4000: short_side=400, target = 1_200_000 // 400 = 3000, ceil(4000/3000) = 2
    assert len(tiles) == 2
    total_pixels = 0
    for tile_bytes in tiles:
        img = Image.open(io.BytesIO(tile_bytes))
        total_pixels += img.size[0] * img.size[1]
    assert total_pixels <= settings.image.tall_image.max_total_pixels


def test_prepare_for_model_respects_max_total_pixels_budget(tmp_path: Path):
    settings = Settings()
    settings.image.tall_image.max_total_pixels = 100_000  # force aggressive shrinking
    path = make_tall_comic(tmp_path / "comic2.jpg", size=(400, 4000))
    loaded = load_image(path)
    tiles = prepare_for_model(loaded.image, settings.image)
    total_pixels = sum(
        Image.open(io.BytesIO(t)).size[0] * Image.open(io.BytesIO(t)).size[1] for t in tiles
    )
    assert total_pixels <= settings.image.tall_image.max_total_pixels * 1.05  # small rounding slack


def test_prepare_for_model_never_upscales(tmp_path: Path):
    settings = Settings()
    path = make_jpeg(tmp_path / "small.jpg", size=(50, 40))
    loaded = load_image(path)
    tiles = prepare_for_model(loaded.image, settings.image)
    img = Image.open(io.BytesIO(tiles[0]))
    assert img.size == (50, 40)


def test_write_output_image_copies_jpeg_verbatim(tmp_path: Path):
    settings = Settings()
    src = make_jpeg(tmp_path / "src.jpg", size=(100, 80))
    loaded = load_image(src)
    assert should_copy_verbatim(loaded, settings.image) is True

    dest = tmp_path / "out" / "dest.jpg"
    file_info = write_output_image(src, loaded, dest, settings.image)
    assert dest.read_bytes() == src.read_bytes()
    assert file_info.format == "jpeg"
    assert file_info.width == 100
    assert file_info.height == 80


def test_write_output_image_reencodes_png(tmp_path: Path):
    settings = Settings()
    src = make_rgba_png(tmp_path / "src.png", size=(64, 48))
    loaded = load_image(src)
    assert should_copy_verbatim(loaded, settings.image) is False

    dest = tmp_path / "out" / "dest.jpg"
    file_info = write_output_image(src, loaded, dest, settings.image)
    assert dest.is_file()
    with Image.open(dest) as img:
        assert img.format == "JPEG"
        assert img.size == (64, 48)
    assert file_info.bytes == dest.stat().st_size
