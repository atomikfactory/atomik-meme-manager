"""Search: operator parsing, FTS5 query building, bm25 ranking, fuzzy fallback, sorts."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

from atomik_meme_web.db import Database
from atomik_meme_web.models import CategoryRef, Item

BM25_WEIGHTS = "0, 4.0, 5.0, 2.0, 2.0, 4.0, 2.0, 1.0"

MAX_QUERY_LENGTH = 200
MAX_QUERY_TERMS = 12
LIKE_ESCAPE_CHAR = "\\"


class QueryTooComplexError(Exception):
    """Raised when `q` exceeds `MAX_QUERY_LENGTH`/`MAX_QUERY_TERMS` (api.py turns this into 400)."""


def escape_like(value: str) -> str:
    r"""Escape `\`, `%`, `_` for a `LIKE ... ESCAPE '\'` clause, so user text can't inject SQL
    wildcards (e.g. a literal `%` or `_` typed into a search box matching everything)."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


ITEM_SELECT = """
SELECT i.*,
       COALESCE(v.view_count, 0) AS view_count,
       v.last_viewed_at AS last_viewed_at,
       (fav.sha256 IS NOT NULL) AS favorite
FROM items i
LEFT JOIN (
    SELECT sha256, COUNT(*) AS view_count, MAX(viewed_at) AS last_viewed_at
    FROM views GROUP BY sha256
) v ON v.sha256 = i.sha256
LEFT JOIN favorites fav ON fav.sha256 = i.sha256
"""

_OPERATOR_RE = re.compile(r"^(type|cat|tag|is):(.+)$", re.IGNORECASE)

_SORTS = {"relevance", "newest", "oldest", "name", "size", "duration", "random"}


def row_to_item(row: sqlite3.Row) -> Item:
    width, height = row["width"], row["height"]
    aspect = (width / height) if width and height else None
    category = None
    if row["category_name"]:
        category = CategoryRef(
            id=row["category_id"], name=row["category_name"], folder=row["category_folder"]
        )
    item_id = row["id"]
    return Item(
        id=item_id,
        path=row["path"],
        rel_path=row["rel_path"],
        name=row["name"],
        stem=row["stem"],
        ext=row["ext"],
        media_type=row["media_type"],
        size_bytes=row["size_bytes"],
        width=width,
        height=height,
        aspect_ratio=aspect,
        duration_s=row["duration_s"],
        fps=row["fps"],
        created_at=row["created_at"],
        modified_at=row["modified_at"],
        indexed_at=row["indexed_at"],
        sha256=row["sha256"],
        dhash=row["dhash"],
        title=row["title"],
        description=row["description"],
        ocr_text=row["ocr_text"],
        tags=json.loads(row["tags_json"] or "[]"),
        topics=json.loads(row["topics_json"] or "[]"),
        tone=json.loads(row["tone_json"] or "[]"),
        meme_type=row["meme_type"],
        template=row["template"],
        category=category,
        nsfw=bool(row["nsfw"]),
        confidence=row["confidence"],
        favorite=bool(row["favorite"]),
        view_count=row["view_count"] or 0,
        last_viewed_at=row["last_viewed_at"],
        thumb_url=f"/api/items/{item_id}/thumb?w=320",
        media_url=f"/api/items/{item_id}/media",
    )


def fetch_item_row(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    return conn.execute(ITEM_SELECT + " WHERE i.id = ?", (item_id,)).fetchone()


def fetch_item_row_by_sha(conn: sqlite3.Connection, sha256: str) -> sqlite3.Row | None:
    return conn.execute(ITEM_SELECT + " WHERE i.sha256 = ? LIMIT 1", (sha256,)).fetchone()


def fetch_rows_by_ids(conn: sqlite3.Connection, ids: list[int]) -> dict[int, sqlite3.Row]:
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(ITEM_SELECT + f" WHERE i.id IN ({placeholders})", ids).fetchall()
    return {row["id"]: row for row in rows}


@dataclass
class Filters:
    type: list[str] = field(default_factory=list)
    category: list[str] = field(default_factory=list)
    tag: list[str] = field(default_factory=list)
    favorite: bool | None = None
    nsfw: str = "exclude"
    min_duration: float | None = None
    max_duration: float | None = None
    since: str | None = None


@dataclass
class SearchParams:
    q: str = ""
    filters: Filters = field(default_factory=Filters)
    sort: str | None = None
    seed: int | None = None
    limit: int = 100
    offset: int = 0


@dataclass
class SearchResult:
    items: list[Item]
    total: int
    took_ms: float
    fuzzy_used: bool


def parse_operators(q: str) -> tuple[str, dict]:
    """Split `type:`/`cat:`/`tag:`/`is:` operators out of `q`; return (remaining text, extra)."""
    tokens = q.split()
    remaining: list[str] = []
    extra: dict = {"type": [], "category": [], "tag": [], "favorite": None, "nsfw": None}
    for tok in tokens:
        match = _OPERATOR_RE.match(tok)
        if not match:
            remaining.append(tok)
            continue
        key, value = match.group(1).lower(), match.group(2)
        if key == "type":
            extra["type"].extend(v.strip().lower() for v in value.split(",") if v.strip())
        elif key == "cat":
            extra["category"].extend(v.strip() for v in value.split(",") if v.strip())
        elif key == "tag":
            extra["tag"].extend(v.strip() for v in value.split(",") if v.strip())
        elif key == "is":
            v = value.strip().lower()
            if v == "fav":
                extra["favorite"] = True
            elif v == "nsfw":
                extra["nsfw"] = "only"
    return " ".join(remaining), extra


def _tokenize(text: str) -> tuple[list[str], list[str]]:
    tokens = [t for t in text.split() if t]
    long_tokens = [t for t in tokens if len(t) >= 3]
    short_tokens = [t for t in tokens if len(t) in (1, 2)]
    return long_tokens, short_tokens


def _escape_fts_term(term: str) -> str:
    return term.replace('"', '""')


class TTLCache:
    def __init__(self, ttl_s: float = 30.0):
        self.ttl_s = ttl_s
        self._data: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> int | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            ts, value = entry
            if time.monotonic() - ts > self.ttl_s:
                del self._data[key]
                return None
            return value

    def set(self, key: str, value: int) -> None:
        with self._lock:
            self._data[key] = (time.monotonic(), value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class FuzzyIndex:
    """In-memory `{id: "name title"}` cache for the rapidfuzz fallback, refreshed after scans."""

    def __init__(self, db: Database):
        self.db = db
        self._cache: dict[int, str] | None = None
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None

    def _ensure(self) -> dict[int, str]:
        with self._lock:
            if self._cache is None:
                rows = self.db.conn.execute("SELECT id, stem, title FROM items").fetchall()
                # Filenames use `-`/`_` as word separators; normalise to spaces so a typo like
                # "kevn hart" scores against "kevin hart reaction", not the punctuated filename.
                self._cache = {
                    row["id"]: f"{row['stem'].replace('-', ' ').replace('_', ' ')} "
                    f"{row['title'] or ''}".strip()
                    for row in rows
                }
            return self._cache

    def match(self, q: str, limit: int = 50, score_cutoff: float = 75.0) -> list[int]:
        cache = self._ensure()
        if not cache:
            return []
        results = process.extract(
            q, cache, scorer=fuzz.WRatio, score_cutoff=score_cutoff, limit=limit
        )
        return [key for _choice, _score, key in results]


def _apply_filters(filters: Filters, where: list[str], params: dict) -> None:
    if filters.type:
        placeholders = []
        for idx, t in enumerate(filters.type):
            key = f"type{idx}"
            placeholders.append(f":{key}")
            params[key] = t
        where.append(f"i.media_type IN ({','.join(placeholders)})")
    if filters.category:
        placeholders = []
        for idx, c in enumerate(filters.category):
            key = f"cat{idx}"
            placeholders.append(f":{key}")
            params[key] = c
        where.append(f"i.category_name IN ({','.join(placeholders)})")
    for idx, t in enumerate(filters.tag):
        key = f"tagval{idx}"
        where.append(
            f"EXISTS (SELECT 1 FROM item_tags it WHERE it.item_id = i.id AND it.tag = :{key})"
        )
        # `item_tags.tag` is always lowercase/trimmed (see indexer.normalize_tag); normalise the
        # filter value the same way so `?tag=Excel` still matches a stored "excel".
        params[key] = t.strip().lower()
    if filters.favorite:
        where.append("fav.sha256 IS NOT NULL")
    if filters.nsfw == "only":
        where.append("i.nsfw = 1")
    elif filters.nsfw != "include":
        # Fail closed: "exclude", or any unrecognised/unexpected value, hides NSFW items. Only
        # the literal "include" shows everything -- never the default for a garbled value.
        where.append("i.nsfw = 0")
    if filters.min_duration is not None:
        where.append("i.duration_s >= :min_duration")
        params["min_duration"] = filters.min_duration
    if filters.max_duration is not None:
        where.append("i.duration_s <= :max_duration")
        params["max_duration"] = filters.max_duration
    if filters.since:
        where.append("i.created_at >= :since")
        params["since"] = filters.since


def search_items(
    conn: sqlite3.Connection,
    params: SearchParams,
    count_cache: TTLCache,
    fuzzy_index: FuzzyIndex,
) -> SearchResult:
    start = time.monotonic()
    if len(params.q) > MAX_QUERY_LENGTH:
        raise QueryTooComplexError(f"query too long (max {MAX_QUERY_LENGTH} characters)")
    if len(params.q.split()) > MAX_QUERY_TERMS:
        raise QueryTooComplexError(f"query has too many terms (max {MAX_QUERY_TERMS})")

    remaining_text, operator_extra = parse_operators(params.q)

    filters = Filters(
        type=[*params.filters.type, *operator_extra["type"]],
        category=[*params.filters.category, *operator_extra["category"]],
        tag=[*params.filters.tag, *operator_extra["tag"]],
        favorite=(
            params.filters.favorite
            if operator_extra["favorite"] is None
            else operator_extra["favorite"]
        ),
        nsfw=(operator_extra["nsfw"] or params.filters.nsfw),
        min_duration=params.filters.min_duration,
        max_duration=params.filters.max_duration,
        since=params.filters.since,
    )

    long_tokens, short_tokens = _tokenize(remaining_text)
    use_fts = bool(long_tokens)

    where: list[str] = []
    sql_params: dict = {}
    joins = ""
    if use_fts:
        joins = "JOIN items_fts ON items_fts.item_id = i.id"
        fts_query = " AND ".join(f'"{_escape_fts_term(t)}"' for t in long_tokens)
        where.append("items_fts MATCH :fts_query")
        sql_params["fts_query"] = fts_query
    for idx, term in enumerate(short_tokens):
        key = f"short{idx}"
        where.append(f"(i.name LIKE :{key} ESCAPE '\\' OR i.title LIKE :{key} ESCAPE '\\')")
        sql_params[key] = f"%{escape_like(term)}%"

    _apply_filters(filters, where, sql_params)
    where_sql = " AND ".join(where) if where else "1=1"

    sort = params.sort
    if sort not in _SORTS:
        sort = "relevance" if use_fts else "newest"

    rank_select = ""
    if sort == "relevance" and use_fts:
        rank_select = f", bm25(items_fts, {BM25_WEIGHTS}) AS rank"
        order_sql = "rank ASC"
    elif sort == "relevance":
        order_sql = "i.created_at DESC"
    elif sort == "newest":
        order_sql = "i.created_at DESC"
    elif sort == "oldest":
        order_sql = "i.created_at ASC"
    elif sort == "name":
        order_sql = "i.name COLLATE NOCASE ASC"
    elif sort == "size":
        order_sql = "i.size_bytes DESC"
    elif sort == "duration":
        order_sql = "i.duration_s DESC"
    else:  # random, seeded so paging is stable
        order_sql = "((i.id * 2654435761 + :seed) % 999999937)"
        sql_params["seed"] = params.seed or 0

    select_sql = f"""
    SELECT i.*,
           COALESCE(v.view_count, 0) AS view_count,
           v.last_viewed_at AS last_viewed_at,
           (fav.sha256 IS NOT NULL) AS favorite
           {rank_select}
    FROM items i
    {joins}
    LEFT JOIN (
        SELECT sha256, COUNT(*) AS view_count, MAX(viewed_at) AS last_viewed_at
        FROM views GROUP BY sha256
    ) v ON v.sha256 = i.sha256
    LEFT JOIN favorites fav ON fav.sha256 = i.sha256
    WHERE {where_sql}
    ORDER BY {order_sql}
    LIMIT :limit OFFSET :offset
    """
    page_params = dict(sql_params)
    page_params["limit"] = params.limit
    page_params["offset"] = params.offset
    rows = conn.execute(select_sql, page_params).fetchall()
    items = [row_to_item(row) for row in rows]

    count_sql = f"""
    SELECT COUNT(*) AS c
    FROM items i
    {joins}
    LEFT JOIN favorites fav ON fav.sha256 = i.sha256
    WHERE {where_sql}
    """
    cache_key = json.dumps(
        {"sql": count_sql, "params": {k: v for k, v in sql_params.items() if k != "seed"}},
        sort_keys=True,
        default=str,
    )
    total = count_cache.get(cache_key)
    if total is None:
        total = conn.execute(count_sql, sql_params).fetchone()["c"]
        count_cache.set(cache_key, total)

    fuzzy_used = False
    # Only on the first page: fuzzy matches aren't part of the SQL result set `offset`/`limit`
    # paginate over, so appending them on later pages would desync paging from `total`.
    if params.offset == 0 and remaining_text and len(remaining_text) >= 3 and len(items) < 5:
        extra_ids = fuzzy_index.match(remaining_text, limit=50)
        existing_ids = {it.id for it in items}
        new_ids = [i for i in extra_ids if i not in existing_ids]
        if new_ids:
            extra_rows = fetch_rows_by_ids(conn, new_ids)
            new_items = [row_to_item(extra_rows[i]) for i in new_ids if i in extra_rows]
            # Keep the page within `limit` and grow `total` by exactly what was appended, so
            # `total` always accounts for every item actually returned (never < len(items)).
            new_items = new_items[: max(0, params.limit - len(items))]
            if new_items:
                fuzzy_used = True
                items.extend(new_items)
                total += len(new_items)

    took_ms = (time.monotonic() - start) * 1000
    return SearchResult(items=items, total=total, took_ms=took_ms, fuzzy_used=fuzzy_used)
