"""`pathtags` provider: category from an ancestor `NN-name` folder (e.g. `00 - NSFW` -> `nsfw`).

Only fills in what `sidecar` left blank: if the sidecar already named a category, this provider
still fills `category_folder` when missing (the physical folder is useful even when the category
name came from metadata), but never overrides a category name the sidecar already supplied.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_CATEGORY_FOLDER_RE = re.compile(r"^\d{2}[\s-]+(.+)$")


def _slugify(name: str) -> str:
    return name.strip().lower().replace(" ", "-")


def _find_category_ancestor(path: Path, library_root: Path) -> str | None:
    try:
        parts = path.relative_to(library_root).parts[:-1]
    except ValueError:
        return None
    for part in parts:
        if _CATEGORY_FOLDER_RE.match(part):
            return part
    return None


class PathTagsProvider:
    name = "pathtags"

    def applies(self, path: Path, fields: dict[str, Any]) -> bool:
        return True

    def extract(
        self, path: Path, rel_path: str, library_root: Path, fields: dict[str, Any]
    ) -> dict[str, Any]:
        folder = _find_category_ancestor(path, library_root)
        if folder is None:
            return {}
        out: dict[str, Any] = {}
        if not fields.get("category_name"):
            match = _CATEGORY_FOLDER_RE.match(folder)
            if match:
                out["category_name"] = _slugify(match.group(1))
        if not fields.get("category_folder"):
            out["category_folder"] = folder
        return out
