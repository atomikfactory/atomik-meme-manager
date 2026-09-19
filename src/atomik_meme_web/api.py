"""FastAPI app factory: routers, static SPA mount, CORS for dev, library selection.

Security note: every non-GET route requires the `X-Atomik-Meme: 1` header (see
`CSRFGuardMiddleware`) so a cross-site page cannot fire blind mutating `fetch()` requests at
this local server -- CORS alone only blocks *reading* the response, not sending the request.

Library selection note: routes read `services.<thing>` (an instance of `services.Services`)
fresh on every call rather than a value captured when the app was built, because `POST
/api/library` mutates that SAME object's fields in place when switching libraries. No route
closure ever holds a stale reference -- see `services.py`.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers

from atomik_meme.schema import CategoriesFile
from atomik_meme_web import actions, fsbrowse, library_state, picker, static
from atomik_meme_web.config import WebConfig
from atomik_meme_web.models import (
    CategoriesResponse,
    CategoryOut,
    CategoryStat,
    CollectionIn,
    CollectionOut,
    CollectionsResponse,
    ConfigResponse,
    DuplicatesResponse,
    FavoriteIn,
    FavoriteResponse,
    FsListResponse,
    HealthResponse,
    InboxResponse,
    InboxUploadError,
    Item,
    ItemDetail,
    LibraryResponse,
    LibrarySwitchIn,
    LibrarySwitchResponse,
    OkResponse,
    RecentLibraryOut,
    RecentResponse,
    ScanProgressOut,
    ScanRecordOut,
    ScanStatusResponse,
    SearchResponse,
    StatsResponse,
    SuggestionOut,
    SuggestResponse,
    TagOut,
    TagsResponse,
    ViewResponse,
)
from atomik_meme_web.search import (
    Filters,
    QueryTooComplexError,
    SearchParams,
    SearchResult,
    escape_like,
    fetch_item_row,
    fetch_item_row_by_sha,
    fetch_rows_by_ids,
    row_to_item,
    search_items,
)
from atomik_meme_web.services import (
    Services,
    build_services,
    close_services_after_delay,
    resolve_data_dir_for_library,
    start_services,
    stop_services,
)

from . import __version__

logger = logging.getLogger(__name__)

DEV_ORIGIN = "http://localhost:5173"
DEFAULT_DIST_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"
CSRF_HEADER_NAME = "x-atomik-meme"
CSRF_HEADER_VALUE = "1"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

NsfwMode = Literal["exclude", "include", "only"]

MEDIA_MIME_OVERRIDES = {
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".gif": "image/gif",
    ".mov": "video/quicktime",
    ".m4v": "video/x-m4v",
    ".avi": "video/x-msvideo",
}
THUMB_CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}
_THUMB_FILENAME_RE = re.compile(r"^([0-9a-f]{64})_(\d+)\.webp$")

LARGE_FOLDER_SAMPLE_LIMIT = 2000
LARGE_FOLDER_THRESHOLD = 100_000
SWITCH_CANCEL_TIMEOUT_S = 10.0
SWITCH_CANCEL_POLL_S = 0.05
OLD_SERVICES_CLOSE_DELAY_S = 5.0


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _own_origins(host: str, port: int) -> set[str]:
    """The origin(s) a browser would use to reach this server "as bound".

    A bind-all host (`0.0.0.0`/`::`) isn't itself a usable Origin -- a same-machine browser
    reaches it via `127.0.0.1`/`localhost`, so those are accepted too in that case.
    """
    hosts = {host}
    if host in ("0.0.0.0", "::"):  # noqa: S104 - recognising the bind-all address, not binding it
        hosts |= {"127.0.0.1", "localhost", "[::1]"}
    return {f"http://{h}:{port}" for h in hosts}


class CSRFGuardMiddleware:
    """Plain ASGI middleware requiring `X-Atomik-Meme: 1` (and a matching Origin, if sent) on every
    non-GET/HEAD/OPTIONS request. See the module docstring."""

    def __init__(self, app, own_origins: set[str]):
        self.app = app
        self.own_origins = own_origins

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope["method"] in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if headers.get(CSRF_HEADER_NAME) != CSRF_HEADER_VALUE:
            response = JSONResponse(
                status_code=403, content={"detail": "missing X-Atomik-Meme header"}
            )
            await response(scope, receive, send)
            return

        origin = headers.get("origin")
        if origin is not None and origin not in self.own_origins and origin != DEV_ORIGIN:
            response = JSONResponse(status_code=403, content={"detail": "origin not allowed"})
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


def _build_search_params(
    *,
    q: str,
    type: str | None,  # noqa: A002
    category: str | None,
    tag: str | None,
    favorite: bool | None,
    nsfw: NsfwMode,
    sort: str | None = None,
    seed: int | None = None,
    limit: int = 100,
    offset: int = 0,
    min_duration: float | None,
    max_duration: float | None,
    since: str | None,
) -> SearchParams:
    return SearchParams(
        q=q,
        filters=Filters(
            type=_csv(type),
            category=_csv(category),
            tag=_csv(tag),
            favorite=favorite,
            nsfw=nsfw,
            min_duration=min_duration,
            max_duration=max_duration,
            since=since,
        ),
        sort=sort,
        seed=seed,
        limit=limit,
        offset=offset,
    )


def _run_search(services: Services, params: SearchParams) -> SearchResult:
    if services.db is None:
        return SearchResult(items=[], total=0, took_ms=0.0, fuzzy_used=False)
    try:
        return search_items(services.db.conn, params, services.count_cache, services.fuzzy_index)
    except QueryTooComplexError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _scan_record_out(record) -> ScanRecordOut | None:
    if record is None:
        return None
    return ScanRecordOut(
        id=record.id,
        started_at=record.started_at,
        finished_at=record.finished_at,
        total=record.total,
        added=record.added,
        modified=record.modified,
        removed=record.removed,
        moved=record.moved,
        duration_s=record.duration_s,
        error=record.error,
    )


def _large_folder_warning(
    path: Path,
    sample_limit: int = LARGE_FOLDER_SAMPLE_LIMIT,
    threshold: int = LARGE_FOLDER_THRESHOLD,
) -> str | None:
    """Best-effort: sample up to `sample_limit` filesystem entries under `path`; if the
    walk finishes within that sample, warn on an actual file count over `threshold`; otherwise
    extrapolate from how many files already turned up in just that first slice. Never raises."""
    try:
        file_count = 0
        entry_count = 0
        finished = True
        for _dirpath, dirnames, filenames in os.walk(path):
            entry_count += len(dirnames) + len(filenames)
            file_count += len(filenames)
            if entry_count >= sample_limit:
                finished = False
                break
        if finished:
            return "large folder" if file_count > threshold else None
        if file_count > sample_limit // 2:
            return "large folder"
        return None
    except OSError:
        return None


_UNSET_LIBRARY = object()


def create_app(
    config: WebConfig, *, library: Path | None = _UNSET_LIBRARY, dist_dir: Path | None = None
) -> FastAPI:
    """Build the FastAPI app: services bundle, middleware, and every route.

    `library`: the resolved startup library. When omitted, resolved via the full precedence
    chain (`library_state.resolve_startup_library` -- the CLI flag is `cli.py`'s job, applied
    before calling this). Pass `None` explicitly to force no-library-mode
    startup regardless of state file/config.
    """
    if library is _UNSET_LIBRARY:
        library = library_state.resolve_startup_library(config, cli_library=None)

    services = build_services(config, library)
    native_picker = picker.probe_native_picker()
    switch_lock = threading.Lock()
    picker_lock = threading.Lock()

    stop_periodic = threading.Event()

    def _periodic_loop() -> None:
        interval = config.scan_interval_min * 60
        while not stop_periodic.wait(interval):
            if services.indexer is not None:
                services.indexer.try_start_background()

    timer_thread: threading.Thread | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal timer_thread
        start_services(services, config)
        if config.scan_interval_min > 0:
            timer_thread = threading.Thread(
                target=_periodic_loop, daemon=True, name="atomik-meme-web-periodic-scan"
            )
            timer_thread.start()
        yield
        stop_periodic.set()
        if timer_thread is not None:
            timer_thread.join(timeout=5.0)
        stop_services(services, join_timeout=5.0)

    app = FastAPI(title="atomik-meme-web", version=__version__, lifespan=lifespan)
    app.state.config = config
    app.state.services = services
    # One-time snapshot of the startup bundle, for convenience/backward compatibility with code
    # that never switches libraries. After a switch these go stale -- use `app.state.services`
    # (the live bundle) for anything that must reflect the current library.
    app.state.db = services.db
    app.state.indexer = services.indexer
    app.state.thumbs = services.thumbs
    app.state.fuzzy_index = services.fuzzy_index
    app.state.duplicates_service = services.duplicates_service
    app.state.count_cache = services.count_cache

    # --- CSRF protection -------------------------------------------------------
    # This is a local, single-user server with no auth, so the only defence a mutating request
    # needs is proof it didn't come from an unrelated web page's blind `fetch()`: a custom header
    # a cross-site page cannot attach without triggering a CORS preflight, which the server only
    # answers for the dev origin. Registered *before* CORSMiddleware so CORS ends up the
    # outermost layer -- OPTIONS preflights are answered by CORSMiddleware without ever reaching
    # this check, and rejections here still carry CORS headers for the dev origin.
    #
    # This is a plain ASGI middleware, not `@app.middleware("http")`/`BaseHTTPMiddleware`:
    # BaseHTTPMiddleware is documented to interfere with FastAPI's own exception-handler
    # dispatch for errors raised deeper in the stack (an unhandled exception below it can bypass
    # `@app.exception_handler`), which would defeat the global 500 handler just below.
    app.add_middleware(CSRFGuardMiddleware, own_origins=_own_origins(config.host, config.port))

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[DEV_ORIGIN],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["Content-Type", "X-Atomik-Meme"],
    )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception for %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    api = APIRouter()

    # --- health / config / stats ------------------------------------------------

    @api.get("/health", response_model=HealthResponse)
    def get_health() -> HealthResponse:
        if services.db is not None:
            count = services.db.conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        else:
            count = 0
        return HealthResponse(
            status="ok",
            version=__version__,
            library_root=str(services.library) if services.library is not None else None,
            data_dir=str(services.data_dir) if services.data_dir is not None else None,
            ffmpeg=shutil.which("ffmpeg") is not None,
            items=count,
        )

    @api.get("/config", response_model=ConfigResponse)
    def get_config() -> ConfigResponse:
        return ConfigResponse(
            inbox_enabled=config.inbox_dir is not None,
            nsfw_default=config.nsfw_default,
            thumb_sizes=config.thumb_sizes,
            library_root=str(services.library) if services.library is not None else None,
        )

    @api.get("/stats", response_model=StatsResponse)
    def get_stats() -> StatsResponse:
        if services.db is None:
            return StatsResponse(
                total=0,
                by_type={"image": 0, "gif": 0, "video": 0},
                favorites=0,
                total_bytes=0,
                nsfw=0,
                categories=[],
                last_scan=None,
                scanning=False,
            )
        conn = services.db.conn
        total = conn.execute("SELECT COUNT(*) c FROM items").fetchone()["c"]
        by_type = {
            row["media_type"]: row["c"]
            for row in conn.execute(
                "SELECT media_type, COUNT(*) c FROM items GROUP BY media_type"
            ).fetchall()
        }
        favorites = conn.execute("SELECT COUNT(*) c FROM favorites").fetchone()["c"]
        total_bytes = conn.execute("SELECT COALESCE(SUM(size_bytes),0) s FROM items").fetchone()[
            "s"
        ]
        nsfw = conn.execute("SELECT COUNT(*) c FROM items WHERE nsfw=1").fetchone()["c"]
        cats = conn.execute(
            """SELECT category_name AS name, category_folder AS folder, COUNT(*) AS count
               FROM items WHERE category_name IS NOT NULL
               GROUP BY category_name ORDER BY category_name"""
        ).fetchall()
        return StatsResponse(
            total=total,
            by_type={
                "image": by_type.get("image", 0),
                "gif": by_type.get("gif", 0),
                "video": by_type.get("video", 0),
            },
            favorites=favorites,
            total_bytes=total_bytes,
            nsfw=nsfw,
            categories=[
                CategoryStat(name=r["name"], folder=r["folder"], count=r["count"]) for r in cats
            ],
            last_scan=_scan_record_out(services.indexer.last_scan if services.indexer else None),
            scanning=services.indexer.running if services.indexer else False,
        )

    # --- items / search -----------------------------------------------------------

    @api.get("/items", response_model=SearchResponse)
    def list_items(
        q: str = "",
        type: str | None = None,  # noqa: A002
        category: str | None = None,
        tag: str | None = None,
        favorite: bool | None = None,
        nsfw: NsfwMode = "exclude",
        sort: str | None = None,
        seed: int | None = None,
        limit: int = 100,
        offset: int = 0,
        min_duration: float | None = None,
        max_duration: float | None = None,
        since: str | None = None,
    ) -> SearchResponse:
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        params = _build_search_params(
            q=q,
            type=type,
            category=category,
            tag=tag,
            favorite=favorite,
            nsfw=nsfw,
            sort=sort,
            seed=seed,
            limit=limit,
            offset=offset,
            min_duration=min_duration,
            max_duration=max_duration,
            since=since,
        )
        result = _run_search(services, params)
        return SearchResponse(
            items=result.items,
            total=result.total,
            limit=limit,
            offset=offset,
            took_ms=result.took_ms,
            fuzzy_used=result.fuzzy_used,
        )

    @api.get("/random", response_model=Item)
    def get_random(
        q: str = "",
        type: str | None = None,  # noqa: A002
        category: str | None = None,
        tag: str | None = None,
        favorite: bool | None = None,
        nsfw: NsfwMode = "exclude",
        min_duration: float | None = None,
        max_duration: float | None = None,
        since: str | None = None,
    ) -> Item:
        import random as _random

        params = _build_search_params(
            q=q,
            type=type,
            category=category,
            tag=tag,
            favorite=favorite,
            nsfw=nsfw,
            sort="random",
            seed=_random.randint(0, 2**31 - 1),
            limit=1,
            offset=0,
            min_duration=min_duration,
            max_duration=max_duration,
            since=since,
        )
        result = _run_search(services, params)
        if not result.items:
            raise HTTPException(status_code=404, detail="no items match")
        return result.items[0]

    @api.get("/recent", response_model=RecentResponse)
    def get_recent(kind: str = "added", limit: int = 50) -> RecentResponse:
        if services.db is None:
            return RecentResponse(items=[])
        limit = max(1, min(limit, 500))
        conn = services.db.conn
        if kind == "viewed":
            rows = conn.execute(
                """SELECT i.*, COALESCE(v.view_count,0) AS view_count,
                          v.last_viewed_at AS last_viewed_at,
                          (fav.sha256 IS NOT NULL) AS favorite
                   FROM items i
                   JOIN (SELECT sha256, COUNT(*) AS view_count,
                                MAX(viewed_at) AS last_viewed_at
                         FROM views GROUP BY sha256) v ON v.sha256 = i.sha256
                   LEFT JOIN favorites fav ON fav.sha256 = i.sha256
                   ORDER BY v.last_viewed_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT i.*, COALESCE(v.view_count,0) AS view_count,
                          v.last_viewed_at AS last_viewed_at,
                          (fav.sha256 IS NOT NULL) AS favorite
                   FROM items i
                   LEFT JOIN (SELECT sha256, COUNT(*) AS view_count,
                                     MAX(viewed_at) AS last_viewed_at
                              FROM views GROUP BY sha256) v ON v.sha256 = i.sha256
                   LEFT JOIN favorites fav ON fav.sha256 = i.sha256
                   ORDER BY i.indexed_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return RecentResponse(items=[row_to_item(r) for r in rows])

    @api.get("/tags", response_model=TagsResponse)
    def get_tags(q: str = "", limit: int = 50) -> TagsResponse:
        if services.db is None:
            return TagsResponse(tags=[])
        limit = max(1, min(limit, 500))
        conn = services.db.conn
        if q:
            rows = conn.execute(
                """SELECT tag, COUNT(*) AS count
                   FROM item_tags
                   WHERE tag LIKE ? ESCAPE '\\'
                   GROUP BY tag ORDER BY count DESC, tag ASC LIMIT ?""",
                (f"%{escape_like(q)}%", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT tag, COUNT(*) AS count
                   FROM item_tags
                   GROUP BY tag ORDER BY count DESC, tag ASC LIMIT ?""",
                (limit,),
            ).fetchall()
        return TagsResponse(tags=[TagOut(tag=r["tag"], count=r["count"]) for r in rows])

    @api.get("/categories", response_model=CategoriesResponse)
    def get_categories() -> CategoriesResponse:
        if services.db is None:
            return CategoriesResponse(categories=[])
        conn = services.db.conn
        counts = {
            r["category_name"]: r["c"]
            for r in conn.execute(
                "SELECT category_name, COUNT(*) c FROM items "
                "WHERE category_name IS NOT NULL GROUP BY category_name"
            ).fetchall()
        }
        cats_path = services.library / "categories.json" if services.library else None
        if cats_path is not None and cats_path.is_file():
            try:
                data = CategoriesFile.read(cats_path)
                return CategoriesResponse(
                    categories=[
                        CategoryOut(
                            id=c.id,
                            name=c.name,
                            folder=c.folder,
                            count=counts.get(c.name, c.count),
                            pinned=c.pinned,
                        )
                        for c in data.categories
                    ]
                )
            except Exception:
                logger.warning("failed to read %s; deriving categories from the index", cats_path)
        rows = conn.execute(
            """SELECT category_id, category_name, category_folder, COUNT(*) c
               FROM items WHERE category_name IS NOT NULL
               GROUP BY category_name ORDER BY category_name"""
        ).fetchall()
        return CategoriesResponse(
            categories=[
                CategoryOut(
                    id=r["category_id"],
                    name=r["category_name"],
                    folder=r["category_folder"],
                    count=r["c"],
                    pinned=False,
                )
                for r in rows
            ]
        )

    @api.get("/suggest", response_model=SuggestResponse)
    def get_suggest(q: str, limit: int = 8) -> SuggestResponse:
        if not q or services.db is None:
            return SuggestResponse(suggestions=[])
        limit = max(1, min(limit, 50))
        conn = services.db.conn
        like = f"%{escape_like(q)}%"
        suggestions: list[SuggestionOut] = []

        for row in conn.execute(
            "SELECT id, title FROM items WHERE title LIKE ? ESCAPE '\\' LIMIT ?", (like, limit)
        ).fetchall():
            suggestions.append(SuggestionOut(kind="title", text=row["title"], item_id=row["id"]))

        seen_tags: set[str] = set()
        for row in conn.execute(
            "SELECT DISTINCT tag FROM item_tags WHERE tag LIKE ? ESCAPE '\\' LIMIT ?",
            (like, limit),
        ).fetchall():
            if row["tag"] not in seen_tags:
                seen_tags.add(row["tag"])
                suggestions.append(SuggestionOut(kind="tag", text=row["tag"]))

        for row in conn.execute(
            "SELECT id, name FROM items WHERE name LIKE ? ESCAPE '\\' LIMIT ?", (like, limit)
        ).fetchall():
            suggestions.append(SuggestionOut(kind="file", text=row["name"], item_id=row["id"]))

        for row in conn.execute(
            "SELECT DISTINCT category_name FROM items"
            " WHERE category_name LIKE ? ESCAPE '\\' LIMIT ?",
            (like, limit),
        ).fetchall():
            suggestions.append(SuggestionOut(kind="category", text=row["category_name"]))

        return SuggestResponse(suggestions=suggestions[:limit])

    # --- item detail / thumb / media -----------------------------------------------

    @api.get("/items/{item_id}", response_model=ItemDetail)
    def get_item_detail(item_id: int) -> ItemDetail:
        if services.db is None:
            raise HTTPException(status_code=404, detail="item not found")
        conn = services.db.conn
        row = fetch_item_row(conn, item_id)
        if row is None:
            raise HTTPException(status_code=404, detail="item not found")
        item = row_to_item(row)

        sidecar_obj = None
        if row["sidecar_path"]:
            try:
                with open(row["sidecar_path"], encoding="utf-8") as fh:
                    sidecar_obj = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("failed to read sidecar %s: %s", row["sidecar_path"], exc)

        dup_ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM items WHERE sha256=? AND id<>?", (row["sha256"], item_id)
            ).fetchall()
        ]
        dup_rows = fetch_rows_by_ids(conn, dup_ids)
        duplicates = [row_to_item(dup_rows[i]) for i in dup_ids if i in dup_rows]

        return ItemDetail(**item.model_dump(), sidecar=sidecar_obj, duplicates=duplicates)

    def _thumb_response(row: sqlite3.Row, w: int) -> FileResponse:
        dest = services.thumbs.get_or_create(row, w)
        return FileResponse(dest, media_type="image/webp", headers=dict(THUMB_CACHE_HEADERS))

    @api.get("/items/{item_id}/thumb")
    def get_item_thumb(item_id: int, w: int = 320) -> FileResponse:
        if services.db is None:
            raise HTTPException(status_code=404, detail="item not found")
        row = fetch_item_row(services.db.conn, item_id)
        if row is None:
            raise HTTPException(status_code=404, detail="item not found")
        # `w` is snapped to the nearest configured `thumb_sizes` value inside `get_or_create`
        # (only those files can ever exist), so an arbitrary/hostile width never triggers a
        # distinct decode.
        return _thumb_response(row, w)

    @api.get("/thumbs/{filename}")
    def get_thumb_by_filename(filename: str) -> FileResponse:
        if services.db is None:
            raise HTTPException(status_code=404, detail="not found")
        match = _THUMB_FILENAME_RE.match(filename)
        if not match:
            raise HTTPException(status_code=404, detail="not found")
        sha256, w = match.group(1), int(match.group(2))
        row = fetch_item_row_by_sha(services.db.conn, sha256)
        if row is None:
            raise HTTPException(status_code=404, detail="item not found")
        return _thumb_response(row, w)

    @api.get("/items/{item_id}/media")
    def get_item_media(item_id: int) -> FileResponse:
        if services.db is None:
            raise HTTPException(status_code=404, detail="item not found")
        row = services.db.conn.execute(
            "SELECT path, ext, name FROM items WHERE id=?", (item_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="item not found")
        path = Path(row["path"])
        if not path.is_file():
            raise HTTPException(status_code=404, detail="media file missing on disk")
        ext = row["ext"]
        media_type = (
            MEDIA_MIME_OVERRIDES.get(ext)
            or mimetypes.guess_type(row["name"])[0]
            or ("application/octet-stream")
        )
        return FileResponse(
            path, media_type=media_type, filename=row["name"], content_disposition_type="inline"
        )

    # --- per-item actions -----------------------------------------------------------

    @api.post("/items/{item_id}/view", response_model=ViewResponse)
    def post_view(item_id: int) -> ViewResponse:
        if services.db is None:
            raise HTTPException(status_code=404, detail="item not found")
        row = services.db.conn.execute("SELECT sha256 FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="item not found")
        now = _utcnow_iso()
        with services.db.write() as conn:
            conn.execute(
                "INSERT INTO views (sha256, viewed_at) VALUES (?, ?)", (row["sha256"], now)
            )
            agg = conn.execute(
                "SELECT COUNT(*) c, MAX(viewed_at) m FROM views WHERE sha256=?", (row["sha256"],)
            ).fetchone()
        return ViewResponse(view_count=agg["c"], last_viewed_at=agg["m"])

    @api.put("/items/{item_id}/favorite", response_model=FavoriteResponse)
    def put_favorite(item_id: int, body: FavoriteIn) -> FavoriteResponse:
        if services.db is None:
            raise HTTPException(status_code=404, detail="item not found")
        row = services.db.conn.execute("SELECT sha256 FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="item not found")
        with services.db.write() as conn:
            if body.favorite:
                conn.execute(
                    "INSERT OR IGNORE INTO favorites (sha256, created_at) VALUES (?, ?)",
                    (row["sha256"], _utcnow_iso()),
                )
            else:
                conn.execute("DELETE FROM favorites WHERE sha256=?", (row["sha256"],))
        return FavoriteResponse(favorite=body.favorite)

    @api.post("/items/{item_id}/open", response_model=OkResponse)
    def post_open(item_id: int) -> OkResponse:
        if services.db is None:
            raise HTTPException(status_code=404, detail="item not found")
        row = services.db.conn.execute("SELECT path FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="item not found")
        path = Path(row["path"])
        if not path.is_file():
            raise HTTPException(status_code=404, detail="media file missing on disk")
        actions.open_path(path)
        return OkResponse(ok=True)

    @api.post("/items/{item_id}/reveal", response_model=OkResponse)
    def post_reveal(item_id: int) -> OkResponse:
        if services.db is None:
            raise HTTPException(status_code=404, detail="item not found")
        row = services.db.conn.execute("SELECT path FROM items WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="item not found")
        path = Path(row["path"])
        if not path.is_file():
            raise HTTPException(status_code=404, detail="media file missing on disk")
        actions.reveal_path(path)
        return OkResponse(ok=True)

    # --- duplicates -------------------------------------------------------------

    @api.get("/duplicates", response_model=DuplicatesResponse)
    def get_duplicates(near: bool = True, max_distance: int = 6) -> DuplicatesResponse:
        if services.duplicates_service is None:
            return DuplicatesResponse(groups=[], computed_at=_utcnow_iso())
        result = services.duplicates_service.compute(near, max_distance)
        return DuplicatesResponse(**result)

    # --- collections --------------------------------------------------------------

    def _builtin_collections() -> list[CollectionOut]:
        if services.db is None:
            return [
                CollectionOut(
                    id=f"builtin:{name}", name=label, filters=filters, icon=icon, builtin=True
                )
                for name, label, filters, icon in (
                    ("favorites", "Favorites", {"favorite": True}, "heart"),
                    (
                        "recent-added",
                        "Recently Added",
                        {"sort": "newest", "since_days": 7},
                        "clock",
                    ),
                    ("recent-viewed", "Recently Viewed", {"kind": "viewed"}, "eye"),
                    ("gifs", "GIFs", {"type": ["gif"]}, "gif"),
                    ("videos", "Videos", {"type": ["video"]}, "video"),
                    ("images", "Images", {"type": ["image"]}, "image"),
                    ("nsfw", "NSFW", {"nsfw": "only"}, "warning"),
                    ("duplicates", "Duplicates", {}, "copy"),
                )
            ]
        conn = services.db.conn
        since_7d = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        counts = {
            "favorites": conn.execute("SELECT COUNT(*) c FROM favorites").fetchone()["c"],
            "gifs": conn.execute("SELECT COUNT(*) c FROM items WHERE media_type='gif'").fetchone()[
                "c"
            ],
            "videos": conn.execute(
                "SELECT COUNT(*) c FROM items WHERE media_type='video'"
            ).fetchone()["c"],
            "images": conn.execute(
                "SELECT COUNT(*) c FROM items WHERE media_type='image'"
            ).fetchone()["c"],
            "nsfw": conn.execute("SELECT COUNT(*) c FROM items WHERE nsfw=1").fetchone()["c"],
            "recent-added": conn.execute(
                "SELECT COUNT(*) c FROM items WHERE created_at >= ?", (since_7d,)
            ).fetchone()["c"],
        }
        builtins = [
            CollectionOut(
                id="builtin:favorites",
                name="Favorites",
                filters={"favorite": True},
                icon="heart",
                builtin=True,
                count=counts["favorites"],
            ),
            CollectionOut(
                id="builtin:recent-added",
                name="Recently Added",
                filters={"sort": "newest", "since_days": 7},
                icon="clock",
                builtin=True,
                count=counts["recent-added"],
            ),
            CollectionOut(
                id="builtin:recent-viewed",
                name="Recently Viewed",
                filters={"kind": "viewed"},
                icon="eye",
                builtin=True,
            ),
            CollectionOut(
                id="builtin:gifs",
                name="GIFs",
                filters={"type": ["gif"]},
                icon="gif",
                builtin=True,
                count=counts["gifs"],
            ),
            CollectionOut(
                id="builtin:videos",
                name="Videos",
                filters={"type": ["video"]},
                icon="video",
                builtin=True,
                count=counts["videos"],
            ),
            CollectionOut(
                id="builtin:images",
                name="Images",
                filters={"type": ["image"]},
                icon="image",
                builtin=True,
                count=counts["images"],
            ),
            CollectionOut(
                id="builtin:nsfw",
                name="NSFW",
                filters={"nsfw": "only"},
                icon="warning",
                builtin=True,
                count=counts["nsfw"],
            ),
            CollectionOut(
                id="builtin:duplicates", name="Duplicates", filters={}, icon="copy", builtin=True
            ),
        ]
        for cat in get_categories().categories:
            builtins.append(
                CollectionOut(
                    id=f"builtin:category:{cat.name}",
                    name=cat.name,
                    filters={"category": [cat.name]},
                    icon="folder",
                    builtin=True,
                    count=cat.count,
                )
            )
        return builtins

    @api.get("/collections", response_model=CollectionsResponse)
    def get_collections() -> CollectionsResponse:
        saved = []
        if services.db is not None:
            rows = services.db.conn.execute("SELECT * FROM collections ORDER BY id").fetchall()
            saved = [
                CollectionOut(
                    id=r["id"],
                    name=r["name"],
                    query=r["query"],
                    filters=json.loads(r["filters_json"]),
                    icon=r["icon"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                    builtin=False,
                )
                for r in rows
            ]
        return CollectionsResponse(collections=[*_builtin_collections(), *saved])

    @api.post("/collections", response_model=CollectionOut, status_code=201)
    def post_collection(body: CollectionIn) -> CollectionOut:
        if services.db is None:
            raise HTTPException(status_code=400, detail="no library selected")
        now = _utcnow_iso()
        with services.db.write() as conn:
            cur = conn.execute(
                "INSERT INTO collections (name, query, filters_json, icon, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (body.name, body.query, json.dumps(body.filters), body.icon, now, now),
            )
            new_id = cur.lastrowid
        return CollectionOut(
            id=new_id,
            name=body.name,
            query=body.query,
            filters=body.filters,
            icon=body.icon,
            created_at=now,
            updated_at=now,
            builtin=False,
        )

    @api.put("/collections/{collection_id}", response_model=CollectionOut)
    def put_collection(collection_id: str, body: CollectionIn) -> CollectionOut:
        if collection_id.startswith("builtin:"):
            raise HTTPException(status_code=400, detail="cannot modify a built-in collection")
        if services.db is None:
            raise HTTPException(status_code=404, detail="collection not found")
        try:
            cid = int(collection_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="collection not found") from exc
        existing = services.db.conn.execute(
            "SELECT id FROM collections WHERE id=?", (cid,)
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="collection not found")
        now = _utcnow_iso()
        with services.db.write() as conn:
            conn.execute(
                "UPDATE collections SET name=?, query=?, filters_json=?, icon=?, updated_at=?"
                " WHERE id=?",
                (body.name, body.query, json.dumps(body.filters), body.icon, now, cid),
            )
        row = services.db.conn.execute("SELECT * FROM collections WHERE id=?", (cid,)).fetchone()
        return CollectionOut(
            id=row["id"],
            name=row["name"],
            query=row["query"],
            filters=json.loads(row["filters_json"]),
            icon=row["icon"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            builtin=False,
        )

    @api.delete("/collections/{collection_id}", status_code=204)
    def delete_collection(collection_id: str) -> None:
        if collection_id.startswith("builtin:"):
            raise HTTPException(status_code=400, detail="cannot delete a built-in collection")
        if services.db is None:
            raise HTTPException(status_code=404, detail="collection not found")
        try:
            cid = int(collection_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="collection not found") from exc
        with services.db.write() as conn:
            cur = conn.execute("DELETE FROM collections WHERE id=?", (cid,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="collection not found")
        return None

    # --- scan ---------------------------------------------------------------------

    @api.get("/scan/status", response_model=ScanStatusResponse)
    def get_scan_status() -> ScanStatusResponse:
        if services.indexer is None:
            return ScanStatusResponse(running=False, progress=None, last=None)
        progress = services.indexer.progress_snapshot()
        progress_out = (
            ScanProgressOut(
                scanned=progress.scanned,
                total=progress.total,
                added=progress.added,
                modified=progress.modified,
                removed=progress.removed,
                moved=progress.moved,
            )
            if progress is not None
            else None
        )
        return ScanStatusResponse(
            running=services.indexer.running,
            progress=progress_out,
            last=_scan_record_out(services.indexer.last_scan),
        )

    @api.post("/scan")
    def post_scan() -> JSONResponse:
        if services.indexer is None:
            return JSONResponse(status_code=400, content={"detail": "no library selected"})
        started = services.indexer.try_start_background()
        if not started:
            return JSONResponse(status_code=409, content={"running": True})
        return JSONResponse(status_code=202, content={"started": True})

    # --- inbox ----------------------------------------------------------------------

    @api.post("/inbox", response_model=InboxResponse)
    async def post_inbox(files: list[UploadFile] = File(...)) -> InboxResponse:
        if config.inbox_dir is None:
            raise HTTPException(status_code=403, detail="inbox uploads are disabled")
        config.inbox_dir.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        failed: list[InboxUploadError] = []
        for upload in files:
            name = upload.filename or ""
            try:
                safe_name = actions.sanitize_inbox_filename(name)
                dest = actions.unique_destination(config.inbox_dir, safe_name)
                data = await upload.read()
                tmp = dest.with_name(dest.name + ".tmp")
                tmp.write_bytes(data)
                tmp.replace(dest)
                saved.append(dest.name)
            except Exception as exc:  # noqa: BLE001 - one bad file must not 500 the whole batch
                logger.warning("inbox upload failed for %r: %s", name, exc)
                failed.append(InboxUploadError(name=name, error=str(exc)))
        return InboxResponse(saved=saved, inbox_dir=str(config.inbox_dir), failed=failed)

    # --- library selection -----------------------------

    def _switch_library(path_str: str) -> LibrarySwitchResponse:
        candidate = Path(path_str)
        if not candidate.is_absolute():
            raise HTTPException(status_code=400, detail="path must be absolute")
        if not candidate.exists():
            raise HTTPException(status_code=404, detail="path not found")
        if not candidate.is_dir():
            raise HTTPException(status_code=404, detail="path is not a directory")
        try:
            os.listdir(candidate)
        except OSError as exc:
            raise HTTPException(status_code=400, detail=f"path is not readable: {exc}") from exc
        candidate = candidate.resolve()

        data_dirs_to_avoid = [d for d in (services.data_dir,) if d is not None]
        data_dirs_to_avoid.append(resolve_data_dir_for_library(config, candidate))
        for data_dir in data_dirs_to_avoid:
            try:
                candidate.relative_to(data_dir)
            except ValueError:
                continue
            raise HTTPException(
                status_code=400, detail="path is the app's data directory (or inside it)"
            )

        with switch_lock:
            if services.indexer is not None and services.indexer.running:
                services.indexer.cancel()
                deadline = time.monotonic() + SWITCH_CANCEL_TIMEOUT_S
                while services.indexer.running and time.monotonic() < deadline:
                    time.sleep(SWITCH_CANCEL_POLL_S)
                if services.indexer.running:
                    raise HTTPException(status_code=409, detail="scan still running")

            if services.library is not None and candidate == services.library:
                if config.remember_library:
                    library_state.record_library_opened(candidate)
                return LibrarySwitchResponse(
                    library_root=str(services.library),
                    data_dir=str(services.data_dir),
                    scan_started=False,
                )

            warning = _large_folder_warning(candidate)
            old_services = Services(
                library=services.library,
                data_dir=services.data_dir,
                db=services.db,
                indexer=services.indexer,
                thumbs=services.thumbs,
                fuzzy_index=services.fuzzy_index,
                duplicates_service=services.duplicates_service,
                count_cache=services.count_cache,
            )
            new_services = build_services(config, candidate)

            services.library = new_services.library
            services.data_dir = new_services.data_dir
            services.db = new_services.db
            services.indexer = new_services.indexer
            services.thumbs = new_services.thumbs
            services.fuzzy_index = new_services.fuzzy_index
            services.duplicates_service = new_services.duplicates_service
            services.count_cache = new_services.count_cache

            close_services_after_delay(old_services, delay_s=OLD_SERVICES_CLOSE_DELAY_S)

            if config.remember_library:
                library_state.record_library_opened(candidate)

            # Unlike server startup, switching always scans the newly-chosen library: the user
            # just explicitly picked it and expects to see its contents, regardless of
            # `scan_on_start` (which only governs the passive startup scan).
            scan_started = services.indexer.try_start_background() if services.indexer else False

        return LibrarySwitchResponse(
            library_root=str(services.library),
            data_dir=str(services.data_dir),
            scan_started=scan_started,
            warning=warning,
        )

    @api.get("/library", response_model=LibraryResponse)
    def get_library() -> LibraryResponse:
        state = library_state.load_state()
        recent = [
            RecentLibraryOut(path=r.path, last_opened=r.last_opened, exists=Path(r.path).is_dir())
            for r in state.recent
        ]
        return LibraryResponse(
            library_root=str(services.library) if services.library is not None else None,
            data_dir=str(services.data_dir) if services.data_dir is not None else None,
            recent=recent,
            native_picker=native_picker,
            remember=config.remember_library,
        )

    @api.post("/library", response_model=LibrarySwitchResponse)
    def post_library(body: LibrarySwitchIn) -> LibrarySwitchResponse:
        return _switch_library(body.path)

    @api.post("/library/pick")
    async def post_library_pick() -> Response:
        if not picker_lock.acquire(blocking=False):
            return JSONResponse(
                status_code=409, content={"detail": "a folder picker is already open"}
            )
        try:
            try:
                path = await run_in_threadpool(picker.run_folder_picker, config.picker_timeout_s)
            except picker.PickerUnavailableError as exc:
                return JSONResponse(status_code=501, content={"detail": str(exc)})
            except subprocess.TimeoutExpired:
                return JSONResponse(status_code=504, content={"detail": "folder picker timed out"})
        finally:
            picker_lock.release()
        if path is None:
            return Response(status_code=204)
        return JSONResponse(status_code=200, content={"path": path})

    @api.post("/library/reveal", response_model=OkResponse)
    def post_library_reveal() -> OkResponse:
        if services.library is None:
            raise HTTPException(status_code=404, detail="no library selected")
        actions.open_path(services.library)
        return OkResponse(ok=True)

    @api.delete("/library/recent", status_code=204)
    def delete_library_recent(path: str) -> None:
        library_state.remove_recent(path)
        return None

    @api.get("/fs/list", response_model=FsListResponse)
    def get_fs_list(path: str | None = None) -> FsListResponse:
        try:
            result = fsbrowse.list_directory(path)
        except fsbrowse.FsListError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return FsListResponse(**result)

    app.include_router(api, prefix="/api")

    static.mount_spa(app, dist_dir if dist_dir is not None else DEFAULT_DIST_DIR)

    return app
