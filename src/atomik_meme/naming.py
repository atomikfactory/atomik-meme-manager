"""Filename slugification, Windows reserved-name handling, and collision resolution."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

RESERVED_NAMES = (
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

LEADING_ARTICLES = {"a", "an", "the"}

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def is_reserved(name: str) -> bool:
    """True if `name` (without extension) is a Windows reserved device name."""
    return name.lower() in RESERVED_NAMES


def slugify(text: str, max_len: int = 80, drop_leading_articles: bool = True) -> str:
    """Turn free text (typically `analysis.title`) into a filesystem-safe slug.

    1. Optionally drop a leading article ("a"/"an"/"the").
    2. NFKD-normalise, strip diacritics/non-ASCII, lowercase.
    3. Collapse runs of non `[a-z0-9]` characters to a single `-`, trim ends.
    4. Truncate to `max_len` characters at a word (`-`) boundary.
    5. Fall back to "meme" if empty; append "-meme" if it is a reserved device name.
    """
    text = text or ""
    words = text.strip().split()
    if drop_leading_articles and len(words) > 1 and words[0].lower() in LEADING_ARTICLES:
        words = words[1:]
    text = " ".join(words)

    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _NON_ALNUM_RE.sub("-", ascii_text).strip("-")

    if len(slug) > max_len:
        truncated = slug[:max_len]
        if "-" in truncated:
            truncated = truncated.rsplit("-", 1)[0]
        slug = truncated.strip("-")

    if not slug:
        slug = "meme"

    if is_reserved(slug):
        slug = f"{slug}-meme"

    return slug


def resolve_collision(
    slug: str,
    id_: str,
    existing: dict[str, str] | Callable[[str], str | None],
) -> str:
    """Return a stem (no extension) guaranteed not to collide with another id's files.

    `existing` maps an already-used stem to the id that owns it (or is a callable
    doing the same lookup). If `slug` is unused, or already owned by `id_` itself
    (idempotent reruns), it is returned unchanged. Otherwise a short/long suffix of
    `id_` is appended deterministically.
    """

    def owner_of(stem: str) -> str | None:
        if callable(existing):
            return existing(stem)
        return existing.get(stem)

    owner = owner_of(slug)
    if owner is None or owner == id_:
        return slug

    candidate = f"{slug}-{id_[:6]}"
    owner = owner_of(candidate)
    if owner is None or owner == id_:
        return candidate

    return f"{slug}-{id_[:12]}"
