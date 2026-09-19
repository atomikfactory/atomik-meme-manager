"""Pydantic API response/request models: the wire contract for every endpoint.

Field names and shapes are part of the public API - changing one is a breaking change.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MediaType = Literal["image", "gif", "video"]


class CategoryRef(BaseModel):
    id: int | None = None
    name: str
    folder: str | None = None


class Item(BaseModel):
    id: int
    path: str
    rel_path: str
    name: str
    stem: str
    ext: str
    media_type: MediaType
    size_bytes: int
    width: int | None = None
    height: int | None = None
    aspect_ratio: float | None = None
    duration_s: float | None = None
    fps: float | None = None
    created_at: str
    modified_at: str
    indexed_at: str
    sha256: str
    dhash: str | None = None
    title: str | None = None
    description: str | None = None
    ocr_text: str | None = None
    tags: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    tone: list[str] = Field(default_factory=list)
    meme_type: str | None = None
    template: str | None = None
    category: CategoryRef | None = None
    nsfw: bool = False
    confidence: float | None = None
    favorite: bool = False
    view_count: int = 0
    last_viewed_at: str | None = None
    thumb_url: str
    media_url: str


class ItemDetail(Item):
    sidecar: dict[str, Any] | None = None
    duplicates: list[Item] = Field(default_factory=list)


class SearchResponse(BaseModel):
    items: list[Item]
    total: int
    limit: int
    offset: int
    took_ms: float
    fuzzy_used: bool = False


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    library_root: str | None
    data_dir: str | None
    ffmpeg: bool
    items: int


class ConfigResponse(BaseModel):
    inbox_enabled: bool
    nsfw_default: str
    thumb_sizes: list[int]
    library_root: str | None


class CategoryStat(BaseModel):
    name: str
    folder: str | None = None
    count: int


class ScanRecordOut(BaseModel):
    id: int | None = None
    started_at: str
    finished_at: str | None = None
    total: int | None = None
    added: int | None = None
    modified: int | None = None
    removed: int | None = None
    moved: int | None = None
    duration_s: float | None = None
    error: str | None = None


class StatsResponse(BaseModel):
    total: int
    by_type: dict[str, int]
    favorites: int
    total_bytes: int
    nsfw: int
    categories: list[CategoryStat]
    last_scan: ScanRecordOut | None = None
    scanning: bool = False


class ScanProgressOut(BaseModel):
    scanned: int
    total: int
    added: int
    modified: int
    removed: int
    moved: int


class ScanStatusResponse(BaseModel):
    running: bool
    progress: ScanProgressOut | None = None
    last: ScanRecordOut | None = None


class ViewResponse(BaseModel):
    view_count: int
    last_viewed_at: str | None = None


class FavoriteIn(BaseModel):
    favorite: bool


class FavoriteResponse(BaseModel):
    favorite: bool


class OkResponse(BaseModel):
    ok: bool = True


class RecentResponse(BaseModel):
    items: list[Item]


class TagOut(BaseModel):
    tag: str
    count: int


class TagsResponse(BaseModel):
    tags: list[TagOut]


class CategoryOut(BaseModel):
    id: int | None = None
    name: str
    folder: str | None = None
    count: int = 0
    pinned: bool = False


class CategoriesResponse(BaseModel):
    categories: list[CategoryOut]


class SuggestionOut(BaseModel):
    kind: Literal["title", "tag", "file", "category"]
    text: str
    item_id: int | None = None


class SuggestResponse(BaseModel):
    suggestions: list[SuggestionOut]


class DuplicateGroup(BaseModel):
    kind: Literal["exact", "near"]
    distance: int
    items: list[Item]


class DuplicatesResponse(BaseModel):
    groups: list[DuplicateGroup]
    computed_at: str


class CollectionIn(BaseModel):
    name: str
    query: str = ""
    filters: dict[str, Any] = Field(default_factory=dict)
    icon: str | None = None


class CollectionOut(BaseModel):
    id: int | str
    name: str
    query: str = ""
    filters: dict[str, Any] = Field(default_factory=dict)
    icon: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    builtin: bool = False
    count: int | None = None


class CollectionsResponse(BaseModel):
    collections: list[CollectionOut]


class ScanStartedResponse(BaseModel):
    started: bool = True


class InboxUploadError(BaseModel):
    name: str
    error: str


class InboxResponse(BaseModel):
    saved: list[str]
    inbox_dir: str
    failed: list[InboxUploadError] = Field(default_factory=list)


# --- Library selection ---------------------------------------------


class RecentLibraryOut(BaseModel):
    path: str
    last_opened: str
    exists: bool


class LibraryResponse(BaseModel):
    library_root: str | None
    data_dir: str | None
    recent: list[RecentLibraryOut] = Field(default_factory=list)
    native_picker: bool
    remember: bool


class LibrarySwitchIn(BaseModel):
    path: str


class LibrarySwitchResponse(BaseModel):
    library_root: str
    data_dir: str
    scan_started: bool
    warning: str | None = None


class LibraryPickResponse(BaseModel):
    path: str


class FsEntryOut(BaseModel):
    name: str
    path: str
    has_children: bool


class FsRootOut(BaseModel):
    name: str
    path: str


class FsListResponse(BaseModel):
    path: str | None
    parent: str | None
    entries: list[FsEntryOut] = Field(default_factory=list)
    roots: list[FsRootOut] = Field(default_factory=list)
    truncated: bool = False
