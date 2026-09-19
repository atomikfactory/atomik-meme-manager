"""The per-library service bundle: one `Services` instance per running server, its fields
swapped in place on a library switch so every route -- which all read `services.<thing>` at call
time, never a captured reference -- sees the new library on its very next request.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from pathlib import Path

from atomik_meme_web.config import WebConfig
from atomik_meme_web.db import Database
from atomik_meme_web.duplicates import DuplicatesService
from atomik_meme_web.indexer import Indexer
from atomik_meme_web.search import FuzzyIndex, TTLCache
from atomik_meme_web.thumbs import ThumbnailCache


def resolve_data_dir_for_library(config: WebConfig, library: Path) -> Path:
    """`<library>/.meme-web`, or -- when `web.data_dir` is explicitly configured -- a per-library
    subfolder `<data_dir>/<sha1(path)[:12]>/` so switching between libraries never has two of
    them sharing one index."""
    if config.data_dir is not None:
        digest = hashlib.sha1(str(library).encode("utf-8")).hexdigest()[:12]  # noqa: S324
        return config.data_dir / digest
    return library / ".meme-web"


@dataclass
class Services:
    """Mutable per-library bundle. `library=None`/`db=None`/... is the valid "no library
    selected yet" state: every route must handle it gracefully."""

    library: Path | None
    data_dir: Path | None = None
    db: Database | None = None
    indexer: Indexer | None = None
    thumbs: ThumbnailCache | None = None
    fuzzy_index: FuzzyIndex | None = None
    duplicates_service: DuplicatesService | None = None
    count_cache: TTLCache = field(default_factory=lambda: TTLCache(ttl_s=30.0))


def build_services(config: WebConfig, library: Path | None) -> Services:
    """Construct a fresh `Services` bundle for `library` (or an empty one when `library is
    None`). Does not start any scan or background thread -- see `services.py` callers."""
    if library is None:
        return Services(library=None)

    data_dir = resolve_data_dir_for_library(config, library)
    data_dir.mkdir(parents=True, exist_ok=True)

    db = Database(data_dir / "index.db")
    indexer = Indexer(db, library, config)
    thumbs = ThumbnailCache(data_dir, db, config.thumb_sizes, config.thumb_retry_after_min)
    fuzzy_index = FuzzyIndex(db)
    duplicates_service = DuplicatesService(db)
    services = Services(
        library=library,
        data_dir=data_dir,
        db=db,
        indexer=indexer,
        thumbs=thumbs,
        fuzzy_index=fuzzy_index,
        duplicates_service=duplicates_service,
    )

    def _after_scan() -> None:
        fuzzy_index.invalidate()
        duplicates_service.invalidate()
        services.count_cache.clear()
        thumbs.prewarm_async()

    indexer.on_scan_complete = _after_scan
    return services


def start_services(services: Services, config: WebConfig) -> bool:
    """Kick off the scan-on-start background scan, if configured and there's a library.

    Returns whether a scan was actually started (used to populate `scan_started` in both the
    startup log and the `POST /api/library` response).
    """
    if services.indexer is not None and config.scan_on_start:
        return services.indexer.try_start_background()
    return False


def stop_services(services: Services, *, join_timeout: float = 5.0) -> None:
    """Join the indexer/prewarm threads and close every DB connection this bundle opened."""
    if services.indexer is not None:
        services.indexer.join(timeout=join_timeout)
    if services.thumbs is not None:
        services.thumbs.join(timeout=join_timeout)
    if services.db is not None:
        services.db.close_all()


def close_services_after_delay(services: Services, delay_s: float = 5.0) -> None:
    """Close a superseded bundle's DB after `delay_s` (in-flight requests using it finish) --
    used when swapping libraries, not at final shutdown (which closes immediately)."""
    if services.db is None:
        return
    timer = threading.Timer(delay_s, services.db.close_all)
    timer.daemon = True
    timer.start()
