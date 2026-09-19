"""Unit tests for the `pathtags` category-folder parsing and dHash determinism."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from atomik_meme_web.providers.pathtags import PathTagsProvider
from atomik_meme_web.thumbs import compute_dhash


def test_pathtags_derives_name_and_folder():
    provider = PathTagsProvider()
    library = Path("/lib")
    path = library / "04-office-software-humor" / "image" / "a.jpg"
    fields = provider.extract(path, "04-office-software-humor/image/a.jpg", library, {})
    assert fields == {
        "category_name": "office-software-humor",
        "category_folder": "04-office-software-humor",
    }


def test_pathtags_nsfw_folder_with_spaces_and_hyphen():
    provider = PathTagsProvider()
    library = Path("/lib")
    path = library / "00 - NSFW" / "image" / "a.jpg"
    fields = provider.extract(path, "00 - NSFW/image/a.jpg", library, {})
    assert fields["category_name"] == "nsfw"
    assert fields["category_folder"] == "00 - NSFW"


def test_pathtags_no_match_returns_empty():
    provider = PathTagsProvider()
    library = Path("/lib")
    path = library / "uncategorised" / "a.jpg"
    assert provider.extract(path, "uncategorised/a.jpg", library, {}) == {}


def test_pathtags_does_not_override_sidecar_category_name():
    provider = PathTagsProvider()
    library = Path("/lib")
    path = library / "04-office-software-humor" / "image" / "a.jpg"
    fields = provider.extract(
        path, "04-office-software-humor/image/a.jpg", library, {"category_name": "custom"}
    )
    assert "category_name" not in fields
    assert fields["category_folder"] == "04-office-software-humor"


def test_dhash_is_deterministic_and_16_hex_chars():
    img = Image.new("RGB", (64, 64), (10, 20, 30))
    h1 = compute_dhash(img)
    h2 = compute_dhash(img)
    assert h1 == h2
    assert len(h1) == 16
    int(h1, 16)  # valid hex


def test_dhash_differs_for_different_images():
    # dHash compares horizontally-adjacent pixels within a row, so a flat image (or one that
    # only varies top-to-bottom) always hashes to all-zero bits regardless of colour; use a
    # left/right split (a real horizontal gradient) to get a non-trivial, distinct hash.
    img1 = Image.new("RGB", (64, 64), (0, 0, 0))
    for x in range(32, 64):
        for y in range(64):
            img1.putpixel((x, y), (255, 255, 255))

    img2 = Image.new("RGB", (64, 64), (0, 0, 0))
    for y in range(32, 64):
        for x in range(64):
            img2.putpixel((x, y), (255, 255, 255))  # top/bottom split: stays all-zero

    h1 = compute_dhash(img1)
    h2 = compute_dhash(img2)
    assert h1 != "0" * 16
    assert h2 == "0" * 16
    assert h1 != h2
