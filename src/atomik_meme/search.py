"""Simple query over sidecar metadata: substring/all-words match, ranked by relevance."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from atomik_meme.index import INDEX_DIR_NAME
from atomik_meme.schema import Sidecar


def _iter_sidecars(output_dir: Path) -> Iterator[Sidecar]:
    output_dir = Path(output_dir)
    if not output_dir.is_dir():
        return
    for json_path in sorted(output_dir.rglob("*.json")):
        try:
            rel_parts = json_path.relative_to(output_dir).parts
        except ValueError:
            continue
        if any(part == INDEX_DIR_NAME for part in rel_parts):
            continue
        if json_path.name == "categories.json":
            continue
        try:
            yield Sidecar.read(json_path)
        except (json.JSONDecodeError, OSError, ValueError):
            continue


def _relevance(sidecar: Sidecar, query_words: list[str]) -> int:
    """Number of text fields that contain all query words (case-insensitive)."""
    if not query_words:
        return 1
    fields = [
        sidecar.analysis.title,
        sidecar.analysis.description,
        sidecar.analysis.ocr_text,
        " ".join(sidecar.analysis.tags),
        " ".join(sidecar.analysis.subjects),
        " ".join(sidecar.analysis.topics),
    ]
    matched = 0
    for field in fields:
        field_lower = field.lower()
        if all(word in field_lower for word in query_words):
            matched += 1
    return matched


def search_collection(
    output_dir: Path,
    query: str = "",
    tags: list[str] | None = None,
    category: str | None = None,
    meme_type: str | None = None,
    media_type: str | None = None,
) -> list[Sidecar]:
    """Search sidecars under `output_dir`, most relevant first, then by filename."""
    query_words = [w.lower() for w in query.split()] if query else []
    wanted_tags = [t.lower() for t in (tags or [])]

    scored: list[tuple[int, Sidecar]] = []
    for sidecar in _iter_sidecars(output_dir):
        if category and (
            sidecar.category is None or sidecar.category.name.lower() != category.lower()
        ):
            continue
        if meme_type and sidecar.analysis.meme_type != meme_type:
            continue
        if media_type and sidecar.file.media_type != media_type:
            continue
        if wanted_tags:
            sidecar_tags = {t.lower() for t in sidecar.analysis.tags}
            if not all(t in sidecar_tags for t in wanted_tags):
                continue

        relevance = _relevance(sidecar, query_words)
        if query_words and relevance == 0:
            continue
        scored.append((relevance, sidecar))

    scored.sort(key=lambda pair: (-pair[0], pair[1].file.name.lower()))
    return [sidecar for _, sidecar in scored]
