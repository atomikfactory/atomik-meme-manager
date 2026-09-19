"""`sidecar` provider: reads `<stem>.json` next to the media file via `atomik_meme.schema.Sidecar`.

On any parse failure the sidecar is logged and skipped (the item still gets `basic`/`pathtags`
metadata) -- a malformed or half-written sidecar must never abort a scan.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from atomik_meme.schema import Sidecar

logger = logging.getLogger(__name__)


class SidecarProvider:
    name = "sidecar"

    def applies(self, path: Path, fields: dict[str, Any]) -> bool:
        return path.with_suffix(".json").is_file()

    def extract(
        self, path: Path, rel_path: str, library_root: Path, fields: dict[str, Any]
    ) -> dict[str, Any]:
        sidecar_path = path.with_suffix(".json")
        try:
            sidecar = Sidecar.read(sidecar_path)
        except Exception as exc:  # noqa: BLE001 - malformed sidecar must not break the scan
            logger.warning("sidecar provider: failed to parse %s: %s", sidecar_path, exc)
            return {}

        analysis = sidecar.analysis
        out: dict[str, Any] = {
            "sidecar_path": str(sidecar_path),
            "sidecar_mtime_ns": sidecar_path.stat().st_mtime_ns,
            "title": analysis.title,
            "description": analysis.description,
            "ocr_text": analysis.ocr_text,
            "tags": list(analysis.tags),
            "topics": list(analysis.topics),
            "tone": list(analysis.tone),
            "meme_type": analysis.meme_type,
            "template": analysis.template,
            "nsfw": bool(analysis.nsfw),
            "confidence": analysis.confidence,
        }
        if sidecar.category is not None:
            out["category_id"] = sidecar.category.id
            out["category_name"] = sidecar.category.name
        return out
