"""The `.meme-manager/index.json` cache, failures.json, and run summaries.

The index is always rebuildable from sidecars, so a missing or corrupt
`index.json` is not a fatal error - `load_or_rebuild` scans the output tree.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from atomik_meme.schema import (
    FailureRecord,
    IndexEntry,
    RunSummary,
    Sidecar,
    atomic_write_json,
)

logger = logging.getLogger(__name__)

INDEX_DIR_NAME = ".meme-manager"


class Index:
    def __init__(self, output_root: Path) -> None:
        self.output_root = Path(output_root)
        self.dir = self.output_root / INDEX_DIR_NAME
        self.path = self.dir / "index.json"
        self.entries: dict[str, IndexEntry] = {}
        self._failures: list[FailureRecord] = []

    @classmethod
    def load_or_rebuild(cls, output_root: Path) -> Index:
        """Load `index.json` (or rebuild it from sidecars if missing/corrupt), then reconcile.

        Reconciliation walks OUTPUT once, regardless of whether the index file itself loaded
        cleanly, and adds an entry for any on-disk sidecar whose sha256 is missing from it.
        `index.json` is flushed only every 10 items during `process`, so a hard crash between
        flushes would otherwise leave sidecars on disk that the index (and therefore both
        resume and filename-collision detection) doesn't know about - risking reprocessing
        and, worse, a fresh file colliding with and overwriting an untracked one.
        """
        idx = cls(output_root)
        if idx.path.is_file():
            try:
                with open(idx.path, encoding="utf-8") as fh:
                    data = json.load(fh)
                idx.entries = {
                    sha: IndexEntry.model_validate(entry)
                    for sha, entry in data.get("entries", {}).items()
                }
            except (json.JSONDecodeError, OSError, ValueError) as exc:
                logger.warning("index.json unreadable (%s); rebuilding from sidecars", exc)
                idx.entries = {}
        idx.reconcile_with_disk(force_flush=True)
        return idx

    def _scan_sidecars(self) -> list[tuple[Path, Sidecar]]:
        results: list[tuple[Path, Sidecar]] = []
        if not self.output_root.is_dir():
            return results
        for json_path in sorted(self.output_root.rglob("*.json")):
            try:
                rel_parts = json_path.relative_to(self.output_root).parts
            except ValueError:
                continue
            if any(part == INDEX_DIR_NAME for part in rel_parts):
                continue
            if json_path.name == "categories.json":
                continue
            try:
                results.append((json_path, Sidecar.read(json_path)))
            except (json.JSONDecodeError, OSError, ValueError) as exc:
                logger.warning("Skipping unreadable sidecar %s: %s", json_path, exc)
        return results

    def reconcile_with_disk(self, force_flush: bool = False) -> int:
        """Add any on-disk sidecar missing from the in-memory index; never removes entries.

        Returns the number of entries added. A fully up-to-date index costs one filesystem
        walk and changes nothing (a no-op flush is skipped unless `force_flush=True`, so a
        normal load stays cheap).
        """
        added = 0
        for json_path, sidecar in self._scan_sidecars():
            sha = sidecar.source.sha256
            if sha in self.entries:
                continue
            # `sidecar.file.name` (not `.with_suffix(".jpg")`) since v2 gif/video pairs keep
            # their original (lowercased) extension - only the image pipeline uses `.jpg`.
            relpath = (
                (json_path.parent / sidecar.file.name).relative_to(self.output_root).as_posix()
            )
            self.entries[sha] = IndexEntry(
                id=sidecar.id,
                sha256=sha,
                stem=json_path.stem,
                relpath=relpath,
                status="done",
                category_id=sidecar.category.id if sidecar.category else None,
                processed_at=sidecar.processing.processed_at,
            )
            added += 1
        if added:
            logger.info("Reconciled %d sidecar(s) missing from index.json", added)
        if added or force_flush:
            self.flush()
        return added

    def has(self, sha256: str) -> bool:
        return sha256 in self.entries

    def get(self, sha256: str) -> IndexEntry | None:
        return self.entries.get(sha256)

    def put(self, entry: IndexEntry) -> None:
        self.entries[entry.sha256] = entry

    def all_stems(self) -> dict[str, str]:
        """Map stem -> owning id, for naming collision checks."""
        return {e.stem: e.id for e in self.entries.values()}

    def flush(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        data = {"entries": {sha: e.model_dump(mode="json") for sha, e in self.entries.items()}}
        atomic_write_json(self.path, data)

    def record_failure(self, source_path: str, stage: str, message: str) -> None:
        self._failures.append(FailureRecord(source_path=source_path, stage=stage, message=message))

    @property
    def failures(self) -> list[FailureRecord]:
        return list(self._failures)

    def write_failures(self) -> Path:
        path = self.dir / "failures.json"
        data = {"failures": [f.model_dump(mode="json") for f in self._failures]}
        atomic_write_json(path, data)
        return path

    def write_run_summary(self, summary: RunSummary) -> Path:
        runs_dir = self.dir / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        path = runs_dir / run_summary_filename(summary)
        summary.write(path)
        return path


def run_summary_filename(summary: RunSummary) -> str:
    """The `<timestamp>-<command>.json` filename `write_run_summary` uses for `summary`."""
    timestamp = summary.started_at.replace("-", "").replace(":", "").replace("T", "-").rstrip("Z")
    return f"{timestamp}-{summary.command}.json"


def run_summary_path(output_root: Path, summary: RunSummary) -> Path:
    """Where `write_run_summary` would place (or has placed) `summary`'s file."""
    return Path(output_root) / INDEX_DIR_NAME / "runs" / run_summary_filename(summary)
