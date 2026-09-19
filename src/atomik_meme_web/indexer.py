"""Scan/index engine: walk, diff, hash, run providers, write, track progress.

Safety: this module only ever writes to `Database` (the `<data_dir>/index.db` file). It never
writes, moves, renames or deletes anything under the library being indexed.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atomik_meme.images import sha256_file
from atomik_meme.schema import CategoriesFile
from atomik_meme_web.config import WebConfig
from atomik_meme_web.db import Database
from atomik_meme_web.providers.base import MetadataProvider
from atomik_meme_web.providers.basic import BasicProvider
from atomik_meme_web.providers.pathtags import PathTagsProvider
from atomik_meme_web.providers.sidecar import SidecarProvider

logger = logging.getLogger(__name__)

HASH_WORKERS = 4
BATCH_SIZE = 200


class ScanAlreadyRunningError(Exception):
    """Raised by `Indexer.scan()` when a scan is already in progress."""


def utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ScanProgress:
    scanned: int = 0
    total: int = 0
    added: int = 0
    modified: int = 0
    removed: int = 0
    moved: int = 0


@dataclass
class ScanRecord:
    started_at: str
    finished_at: str | None = None
    total: int = 0
    added: int = 0
    modified: int = 0
    removed: int = 0
    moved: int = 0
    duration_s: float = 0.0
    error: str | None = None
    id: int | None = None


def default_providers(config: WebConfig) -> list[MetadataProvider]:
    return [BasicProvider(config), SidecarProvider(), PathTagsProvider()]


def _walk_media_files(library: Path, config: WebConfig) -> list[tuple[Path, int, int]]:
    """`os.scandir`-based recursive walk, skipping hidden/ignored directories."""
    ignore = set(config.ignore)
    exts = config.extensions.all_lower()
    results: list[tuple[Path, int, int]] = []
    stack: list[Path] = [library]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            logger.warning("could not list directory %s: %s", current, exc)
            continue
        for entry in entries:
            name = entry.name
            if name.startswith("."):
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_dir:
                if name in ignore:
                    continue
                stack.append(Path(entry.path))
                continue
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
            except OSError:
                continue
            ext = Path(name).suffix.lower()
            if ext not in exts:
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            results.append((Path(entry.path), st.st_size, st.st_mtime_ns))
    return results


def _run_providers(
    providers: list[MetadataProvider], path: Path, rel_path: str, library: Path
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for provider in providers:
        try:
            if provider.applies(path, fields):
                result = provider.extract(path, rel_path, library, fields)
                if result:
                    fields.update(result)
        except Exception:  # noqa: BLE001 - one bad provider must not abort the scan
            logger.exception("%s provider failed for %s", getattr(provider, "name", "?"), path)
    return fields


def _load_nsfw_rule_map(library: Path) -> dict[str, str | None]:
    """`{category_name: rule}` from `<library>/categories.json`, or `{}` if absent/unreadable.

    Used so an item can be recognised as belonging to the pinned NSFW category (`rule: "nsfw"`)
    even when its own resolved `category_name` isn't literally `"nsfw"` (a user-renamed pinned
    category, for instance).
    """
    path = library / "categories.json"
    if not path.is_file():
        return {}
    try:
        data = CategoriesFile.read(path)
    except Exception:  # noqa: BLE001 - a malformed categories.json must not abort the scan
        logger.warning("failed to read %s for NSFW-category lookup", path)
        return {}
    return {category.name: category.rule for category in data.categories}


def _finalize_nsfw(
    fields: dict[str, Any], nsfw_folder: str, nsfw_rule_map: dict[str, str | None]
) -> None:
    """Force `fields["nsfw"] = True` when the item's *category* is the pinned NSFW one.

    The web app treats NSFW-category membership as NSFW regardless of what the sidecar's own
    `analysis.nsfw` says (e.g. a meme placed there by an explicit `--category`/folder hint, not
    because the vision model flagged it): true when the resolved category name is `"nsfw"`, the
    category's ancestor folder matches the configured `categorize.nsfw.folder`, or
    `categories.json` records that category with `rule: "nsfw"`. A sidecar that already says
    `nsfw: true` is left alone (never downgraded).
    """
    if fields.get("nsfw"):
        return
    category_name = fields.get("category_name")
    category_folder = fields.get("category_folder")
    if (
        category_name == "nsfw"
        or (category_folder is not None and category_folder == nsfw_folder)
        or (category_name is not None and nsfw_rule_map.get(category_name) == "nsfw")
    ):
        fields["nsfw"] = True


def _build_item_row(
    path: Path, rel_path: str, fields: dict[str, Any], existing_id: int | None
) -> dict[str, Any]:
    now = utcnow_iso()
    tags = fields.get("tags") or []
    topics = fields.get("topics") or []
    tone = fields.get("tone") or []
    return {
        "id": existing_id,
        "path": str(path),
        "rel_path": rel_path,
        "name": path.name,
        "stem": path.stem,
        "ext": path.suffix.lower(),
        "media_type": fields.get("media_type", "image"),
        "size_bytes": fields.get("size_bytes", 0),
        "mtime_ns": fields.get("mtime_ns", 0),
        "created_at": fields.get("created_at", now),
        "modified_at": fields.get("modified_at", now),
        "width": fields.get("width"),
        "height": fields.get("height"),
        "duration_s": fields.get("duration_s"),
        "fps": fields.get("fps"),
        "frame_count": fields.get("frame_count"),
        "sha256": fields["sha256"],
        "indexed_at": now,
        "sidecar_path": fields.get("sidecar_path"),
        "sidecar_mtime_ns": fields.get("sidecar_mtime_ns"),
        "title": fields.get("title"),
        "description": fields.get("description"),
        "ocr_text": fields.get("ocr_text"),
        "tags_json": json.dumps(tags, ensure_ascii=False),
        "topics_json": json.dumps(topics, ensure_ascii=False),
        "tone_json": json.dumps(tone, ensure_ascii=False),
        "meme_type": fields.get("meme_type"),
        "template": fields.get("template"),
        "category_id": fields.get("category_id"),
        "category_name": fields.get("category_name"),
        "category_folder": fields.get("category_folder"),
        "nsfw": 1 if fields.get("nsfw") else 0,
        "confidence": fields.get("confidence"),
    }


def _fts_tags_blob(item: dict[str, Any]) -> str:
    tags = json.loads(item["tags_json"])
    extra = [t for t in (item.get("meme_type"), item.get("template")) if t]
    return " ".join([*tags, *extra])


def normalize_tag(tag: str) -> str:
    """Lowercase + trim, matching `db.py`'s migration backfill (`LOWER(TRIM(...))` in SQL) so a
    freshly-scanned item and a backfilled-from-`tags_json` one never disagree on a tag's
    spelling."""
    return tag.strip().lower()


def _sync_item_tags(conn, item_id: int, tags: list[str]) -> None:
    """Replace `item_tags` rows for `item_id` -- the normalised (lowercase/trimmed/deduped)
    mirror of `items.tags_json` used by the `tag` filter and `/api/tags`, so they don't need an
    O(N) `json_each` scan. `tags_json`/`Item.tags` themselves keep the original casing for
    display; only this lookup table is normalised.
    """
    conn.execute("DELETE FROM item_tags WHERE item_id=?", (item_id,))
    normalized = sorted({normalize_tag(t) for t in tags if t and t.strip()})
    if normalized:
        conn.executemany(
            "INSERT INTO item_tags (item_id, tag) VALUES (?, ?)",
            [(item_id, t) for t in normalized],
        )


def _upsert_item(conn, item: dict[str, Any]) -> int:
    if item["id"] is None:
        cur = conn.execute(
            """INSERT INTO items (
                path, rel_path, name, stem, ext, media_type, size_bytes, mtime_ns,
                created_at, modified_at, width, height, duration_s, fps, frame_count,
                sha256, dhash, indexed_at, sidecar_path, sidecar_mtime_ns,
                title, description, ocr_text, tags_json, topics_json, tone_json,
                meme_type, template, category_id, category_name, category_folder,
                nsfw, confidence
            ) VALUES (
                :path, :rel_path, :name, :stem, :ext, :media_type, :size_bytes, :mtime_ns,
                :created_at, :modified_at, :width, :height, :duration_s, :fps, :frame_count,
                :sha256, NULL, :indexed_at, :sidecar_path, :sidecar_mtime_ns,
                :title, :description, :ocr_text, :tags_json, :topics_json, :tone_json,
                :meme_type, :template, :category_id, :category_name, :category_folder,
                :nsfw, :confidence
            )""",
            item,
        )
        item_id = cur.lastrowid
    else:
        item_id = item["id"]
        conn.execute(
            """UPDATE items SET
                path=:path, rel_path=:rel_path, name=:name, stem=:stem, ext=:ext,
                media_type=:media_type, size_bytes=:size_bytes, mtime_ns=:mtime_ns,
                created_at=:created_at, modified_at=:modified_at, width=:width, height=:height,
                duration_s=:duration_s, fps=:fps, frame_count=:frame_count, sha256=:sha256,
                dhash=NULL, indexed_at=:indexed_at, sidecar_path=:sidecar_path,
                sidecar_mtime_ns=:sidecar_mtime_ns, title=:title, description=:description,
                ocr_text=:ocr_text, tags_json=:tags_json, topics_json=:topics_json,
                tone_json=:tone_json, meme_type=:meme_type, template=:template,
                category_id=:category_id, category_name=:category_name,
                category_folder=:category_folder, nsfw=:nsfw, confidence=:confidence
            WHERE id=:id""",
            item,
        )
    conn.execute("DELETE FROM items_fts WHERE item_id=?", (item_id,))
    conn.execute(
        """INSERT INTO items_fts
               (item_id, name, title, description, ocr_text, tags, topics, category)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            item_id,
            item["name"],
            item.get("title") or "",
            item.get("description") or "",
            item.get("ocr_text") or "",
            _fts_tags_blob(item),
            " ".join(json.loads(item["topics_json"])),
            item.get("category_name") or "",
        ),
    )
    _sync_item_tags(conn, item_id, json.loads(item["tags_json"]))
    return item_id


class Indexer:
    """Owns the scan lifecycle: diff, hash, provider pipeline, batched writes, progress."""

    def __init__(self, db: Database, library: Path, config: WebConfig):
        self.db = db
        self.library = library
        self.config = config
        self.on_scan_complete: Callable[[], None] | None = None
        self._state_lock = threading.Lock()
        self._running = False
        self._progress: ScanProgress | None = None
        self._last_scan: ScanRecord | None = None
        self._thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._load_last_scan()

    def cancel(self) -> None:
        """Cooperative cancellation: request the current scan stop
        early. Checked between batches and between hashing files; safe to call at any time,
        including when nothing is running."""
        self._cancel_event.set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_event.is_set()

    def _load_last_scan(self) -> None:
        row = self.db.conn.execute("SELECT * FROM scans ORDER BY id DESC LIMIT 1").fetchone()
        if row is not None:
            self._last_scan = ScanRecord(
                id=row["id"],
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                total=row["total"],
                added=row["added"],
                modified=row["modified"],
                removed=row["removed"],
                moved=row["moved"],
                duration_s=row["duration_s"] or 0.0,
                error=row["error"],
            )

    @property
    def running(self) -> bool:
        return self._running

    @property
    def last_scan(self) -> ScanRecord | None:
        return self._last_scan

    def progress_snapshot(self) -> ScanProgress | None:
        return self._progress

    def try_start_background(self) -> bool:
        """Start a scan on a daemon thread; returns False if one is already running."""
        with self._state_lock:
            if self._running:
                return False
            self._running = True
        self._cancel_event.clear()

        def _run() -> None:
            try:
                self._do_scan()
            except Exception:
                logger.exception("background scan failed")
            finally:
                with self._state_lock:
                    self._running = False

        thread = threading.Thread(target=_run, daemon=True, name="atomik-meme-web-scan")
        self._thread = thread
        thread.start()
        return True

    def join(self, timeout: float | None = None) -> None:
        """Wait for a currently-running background scan to finish (used at shutdown)."""
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

    def scan(self) -> ScanRecord:
        """Synchronous one-off scan (used by `atomik-meme-web index` and tests)."""
        with self._state_lock:
            if self._running:
                raise ScanAlreadyRunningError("a scan is already running")
            self._running = True
        self._cancel_event.clear()
        try:
            return self._do_scan()
        finally:
            with self._state_lock:
                self._running = False

    def _do_scan(self) -> ScanRecord:
        start = time.monotonic()
        started_at = utcnow_iso()
        error: str | None = None
        record = ScanRecord(started_at=started_at)
        try:
            record = self._scan_body(started_at)
        except Exception as exc:  # noqa: BLE001 - record the failure, then re-raise
            error = f"{type(exc).__name__}: {exc}"
            record.error = error
            logger.exception("scan failed")
            raise
        finally:
            record.duration_s = time.monotonic() - start
            record.finished_at = utcnow_iso()
            with self.db.write() as conn:
                cur = conn.execute(
                    """INSERT INTO scans
                       (started_at, finished_at, total, added, modified, removed, moved,
                        duration_s, error)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record.started_at,
                        record.finished_at,
                        record.total,
                        record.added,
                        record.modified,
                        record.removed,
                        record.moved,
                        record.duration_s,
                        record.error,
                    ),
                )
                record.id = cur.lastrowid
            self._last_scan = record
            self._progress = None
            if error is None and self.on_scan_complete is not None:
                try:
                    self.on_scan_complete()
                except Exception:
                    logger.exception("on_scan_complete hook failed")
        return record

    def _scan_body(self, started_at: str) -> ScanRecord:
        walked = _walk_media_files(self.library, self.config)
        total = len(walked)
        progress = ScanProgress(total=total)
        self._progress = progress

        conn = self.db.conn
        existing_rows = conn.execute(
            "SELECT id, path, size_bytes, mtime_ns, sha256, sidecar_mtime_ns FROM items"
        ).fetchall()
        existing_by_path = {row["path"]: row for row in existing_rows}

        new_paths: list[Path] = []
        changed_paths: list[Path] = []
        unchanged_refresh: list[tuple[Any, Path]] = []
        seen_paths: set[str] = set()

        for path, size, mtime_ns in walked:
            key = str(path)
            seen_paths.add(key)
            row = existing_by_path.get(key)
            if row is None:
                new_paths.append(path)
            elif row["size_bytes"] != size or row["mtime_ns"] != mtime_ns:
                changed_paths.append(path)
            else:
                sidecar_path = path.with_suffix(".json")
                try:
                    smtime = sidecar_path.stat().st_mtime_ns if sidecar_path.is_file() else None
                except OSError:
                    smtime = None
                if smtime != row["sidecar_mtime_ns"]:
                    unchanged_refresh.append((row, path))

        removed_rows = [
            row for path_key, row in existing_by_path.items() if path_key not in seen_paths
        ]

        to_hash = new_paths + changed_paths
        hashes: dict[str, str] = {}
        if to_hash:
            with ThreadPoolExecutor(max_workers=HASH_WORKERS) as pool:
                future_map = {pool.submit(sha256_file, p): p for p in to_hash}
                for future in as_completed(future_map):
                    p = future_map[future]
                    try:
                        hashes[str(p)] = future.result()
                    except OSError as exc:
                        logger.warning("failed to hash %s: %s", p, exc)
                    if self._cancel_event.is_set():
                        # Stop waiting for the rest; already-running hashes finish (threads can't
                        # be force-killed), but nothing further is dispatched.
                        pool.shutdown(wait=False, cancel_futures=True)
                        break

        # A list per sha256, not a single row: deleting 2+ byte-identical files in the same scan
        # must leave every one of them removed, not just the first (a plain `setdefault` would
        # silently keep only one and leave the rest as ghost rows).
        removed_by_sha: dict[str, list[Any]] = {}
        for row in removed_rows:
            removed_by_sha.setdefault(row["sha256"], []).append(row)

        providers = default_providers(self.config)
        nsfw_rule_map = _load_nsfw_rule_map(self.library)
        added = modified = moved = removed = 0
        batch: list[dict[str, Any]] = []
        processed = 0

        def flush() -> None:
            nonlocal batch
            if not batch:
                return
            with self.db.write() as wconn:
                for row_data in batch:
                    _upsert_item(wconn, row_data)
            batch = []

        def _update_progress() -> None:
            progress.scanned = processed
            progress.added = added
            progress.modified = modified
            progress.removed = removed
            progress.moved = moved

        for path in new_paths:
            if self._cancel_event.is_set():
                break
            h = hashes.get(str(path))
            if h is None:
                continue
            moved_row = None
            pending = removed_by_sha.get(h)
            if pending:
                moved_row = pending.pop(0)
                if not pending:
                    del removed_by_sha[h]
            rel_path = path.relative_to(self.library).as_posix()
            fields = _run_providers(providers, path, rel_path, self.library)
            fields["sha256"] = h
            _finalize_nsfw(fields, self.config.nsfw_category_folder, nsfw_rule_map)
            existing_id = moved_row["id"] if moved_row is not None else None
            if moved_row is not None:
                moved += 1
            else:
                added += 1
            batch.append(_build_item_row(path, rel_path, fields, existing_id))
            processed += 1
            _update_progress()
            if len(batch) >= BATCH_SIZE:
                flush()

        for path in changed_paths:
            if self._cancel_event.is_set():
                break
            h = hashes.get(str(path))
            if h is None:
                continue
            row = existing_by_path[str(path)]
            rel_path = path.relative_to(self.library).as_posix()
            fields = _run_providers(providers, path, rel_path, self.library)
            fields["sha256"] = h
            _finalize_nsfw(fields, self.config.nsfw_category_folder, nsfw_rule_map)
            modified += 1
            batch.append(_build_item_row(path, rel_path, fields, row["id"]))
            processed += 1
            _update_progress()
            if len(batch) >= BATCH_SIZE:
                flush()

        for row, path in unchanged_refresh:
            if self._cancel_event.is_set():
                break
            rel_path = path.relative_to(self.library).as_posix()
            fields = _run_providers(providers, path, rel_path, self.library)
            fields["sha256"] = row["sha256"]
            _finalize_nsfw(fields, self.config.nsfw_category_folder, nsfw_rule_map)
            modified += 1
            batch.append(_build_item_row(path, rel_path, fields, row["id"]))
            processed += 1
            _update_progress()
            if len(batch) >= BATCH_SIZE:
                flush()

        flush()

        if removed_by_sha:
            with self.db.write() as wconn:
                for rows_for_sha in removed_by_sha.values():
                    for row in rows_for_sha:
                        wconn.execute("DELETE FROM items WHERE id=?", (row["id"],))
                        wconn.execute("DELETE FROM items_fts WHERE item_id=?", (row["id"],))
                        wconn.execute("DELETE FROM item_tags WHERE item_id=?", (row["id"],))
                        removed += 1
        _update_progress()

        return ScanRecord(
            started_at=started_at,
            total=total,
            added=added,
            modified=modified,
            removed=removed,
            moved=moved,
        )
