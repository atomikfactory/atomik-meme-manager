"""The `MetadataProvider` protocol: `applies(path, fields) -> bool`, `extract(...) -> dict`.

Providers run in a fixed order (`basic`, `sidecar`, `pathtags`) over an accumulating `fields`
dict; each provider can see what earlier providers already filled in (e.g. `pathtags` only fills
`category_name`/`category_folder` when `sidecar` did not). Adding a new provider (EXIF, OCR, ...)
means one new module plus one entry in `indexer.DEFAULT_PROVIDERS`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class MetadataProvider(Protocol):
    name: str

    def applies(self, path: Path, fields: dict[str, Any]) -> bool:
        """Whether this provider has anything to contribute for `path`."""
        ...

    def extract(
        self, path: Path, rel_path: str, library_root: Path, fields: dict[str, Any]
    ) -> dict[str, Any]:
        """Return a partial dict of fields to merge into the item record."""
        ...
