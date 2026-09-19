"""Pinned-category and layout helpers shared by `process` (hints, NSFW routing),
`categorize` (discovery/apply must never touch pinned members), and `migrate`
(standalone v1 -> v2 layout normalisation, no LLM calls).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date
from pathlib import Path

from rich.console import Console

from atomik_meme.images import detect_media_type
from atomik_meme.index import INDEX_DIR_NAME, Index
from atomik_meme.naming import slugify
from atomik_meme.schema import (
    SCHEMA_VERSION,
    CategoriesFile,
    Category,
    CreatedCategory,
    RunSummary,
    Sidecar,
    new_run_id,
    utcnow_iso,
)

logger = logging.getLogger(__name__)

LAYOUT_VERSION = 2
MEDIA_SUBDIRS = ("image", "gif", "video")
_NN_PREFIX_RE = re.compile(r"^\d{2}\s*-\s*")


def is_pinned_id(category_id: int) -> bool:
    """id 0 (NSFW) and ids 11+ (user categories) are pinned; 1..10 are discovered/others."""
    return category_id == 0 or category_id >= 11


def category_slug(name: str, max_len: int = 30) -> str:
    """The same slugging rule used for discovered-category folders."""
    return slugify(name, max_len=max_len, drop_leading_articles=False)


def category_media_dir(output_dir: Path, category: Category, media_type: str) -> Path:
    return Path(output_dir) / category.folder / media_type


def load_categories_file(output_dir: Path) -> CategoriesFile | None:
    path = Path(output_dir) / "categories.json"
    if not path.is_file():
        return None
    try:
        return CategoriesFile.read(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("categories.json unreadable (%s); ignoring", exc)
        return None


def _empty_categories_file() -> CategoriesFile:
    return CategoriesFile(
        generated_at=utcnow_iso(),
        model="",
        profile="",
        run_id=new_run_id(),
        collection_size=0,
        layout_version=LAYOUT_VERSION,
        categories=[],
    )


def sort_categories(categories: list[Category]) -> list[Category]:
    """Order in the file: id 0, then 1-10, then 11+ (just ascending id)."""
    return sorted(categories, key=lambda c: c.id)


def match_hint(categories: list[Category], hint: str) -> Category | None:
    """Slug-match `hint` against a category's name, folder-minus-`NN-`, or full folder slug."""
    target = category_slug(hint)
    if not target:
        return None
    for cat in categories:
        candidates = {category_slug(cat.name), category_slug(cat.folder)}
        stripped = _NN_PREFIX_RE.sub("", cat.folder)
        candidates.add(category_slug(stripped))
        if target in candidates:
            return cat
    return None


def next_user_category_id(categories: list[Category]) -> int:
    user_ids = [c.id for c in categories if c.id >= 11]
    return max(user_ids, default=10) + 1


def ensure_category(
    output_dir: Path,
    categories_file: CategoriesFile | None,
    *,
    id: int,  # noqa: A002 - matches Category.id
    name: str,
    folder: str,
    description: str,
    pinned: bool,
    rule: str | None,
    source: str,
) -> tuple[CategoriesFile, Category, bool]:
    """Return `(file, category, created)`, writing the file to disk if the category is new."""
    cf = categories_file or _empty_categories_file()
    existing = next((c for c in cf.categories if c.id == id), None)
    if existing is not None:
        return cf, existing, False
    cat = Category(
        id=id,
        name=name,
        folder=folder,
        description=description,
        keywords=[],
        count=0,
        pinned=pinned,
        rule=rule,
        source=source,
    )
    cf.categories = sort_categories([*cf.categories, cat])
    cf.layout_version = LAYOUT_VERSION
    cf.write(Path(output_dir) / "categories.json")
    return cf, cat, True


def plan_nsfw_category(categories_file: CategoriesFile | None, nsfw_cfg) -> Category:
    """The NSFW category as it exists, or as it *would* be created (no write) - used so
    `categorize --dry-run` can preview NSFW routing without touching disk."""
    if categories_file is not None:
        existing = next((c for c in categories_file.categories if c.id == 0), None)
        if existing is not None:
            return existing
    return Category(
        id=0,
        name=nsfw_cfg.name,
        folder=nsfw_cfg.folder,
        description="Not-safe-for-work memes (auto-routed).",
        keywords=[],
        count=0,
        pinned=True,
        rule="nsfw",
        source="rule",
    )


def ensure_nsfw_category(
    output_dir: Path, categories_file: CategoriesFile | None, nsfw_cfg
) -> tuple[CategoriesFile, Category, bool]:
    return ensure_category(
        output_dir,
        categories_file,
        id=0,
        name=nsfw_cfg.name,
        folder=nsfw_cfg.folder,
        description="Not-safe-for-work memes (auto-routed).",
        pinned=True,
        rule="nsfw",
        source="rule",
    )


def ensure_user_category(
    output_dir: Path, categories_file: CategoriesFile | None, hint_name: str
) -> tuple[CategoriesFile, Category, bool]:
    """Resolve `hint_name` to a pinned user category, creating one if nothing matches.

    Guarded against creating a duplicate of any existing category's slug (name, folder minus
    `NN-`, or full folder slug) even if called directly without going through `resolve_hints`'s
    own matching - `match_hint` is the single source of truth for "does this already exist".
    `name` is stored as the slug (matching is by slug, so the category's
    own name should already be in that form); the original text the user typed is preserved in
    `description` instead.
    """
    cats = categories_file.categories if categories_file else []
    existing = match_hint(cats, hint_name)
    if existing is not None:
        return categories_file or _empty_categories_file(), existing, False

    new_id = next_user_category_id(cats)
    slug = category_slug(hint_name)
    folder = f"{new_id:02d}-{slug}"
    today = date.today().isoformat()
    original = hint_name.strip()
    return ensure_category(
        output_dir,
        categories_file,
        id=new_id,
        name=slug,
        folder=folder,
        description=(
            f'User-defined category "{original}" (created from a processing hint on {today}).'
        ),
        pinned=True,
        rule=None,
        source="user",
    )


def resolve_hints(
    output_dir: Path, categories_file: CategoriesFile | None, hint_names: list[str]
) -> tuple[CategoriesFile | None, dict[str, Category], list[Category]]:
    """Resolve each unique hint name to a `Category`, creating pinned user categories for
    any that don't match an existing category. Returns `(file, name -> Category, created)`.
    """
    resolved: dict[str, Category] = {}
    created: list[Category] = []
    cf = categories_file
    seen: dict[str, str] = {}
    for name in hint_names:
        key = category_slug(name)
        if key in seen:
            resolved[name] = resolved[seen[key]]
            continue
        seen[key] = name
        existing = match_hint(cf.categories if cf else [], name)
        if existing is not None:
            resolved[name] = existing
        else:
            cf, cat, was_created = ensure_user_category(output_dir, cf, name)
            resolved[name] = cat
            if was_created:
                created.append(cat)
    return cf, resolved, created


def _cleanup_empty_media_subdirs(category_dir: Path) -> None:
    for sub in MEDIA_SUBDIRS:
        subdir = category_dir / sub
        if subdir.is_dir():
            try:
                if not any(subdir.iterdir()):
                    subdir.rmdir()
            except OSError:
                pass


def normalize_category_layout(output_dir: Path, category: Category, index: Index | None) -> int:
    """Move any pair sitting directly inside `category`'s folder (v1 layout) into the right
    `<image|gif|video>/` subfolder; remove now-empty media subfolders. Idempotent. Returns the
    number of pairs moved.
    """
    output_dir = Path(output_dir)
    cat_dir = output_dir / category.folder
    if not cat_dir.is_dir():
        return 0
    moved = 0
    for json_path in sorted(cat_dir.glob("*.json")):
        if json_path.name == "categories.json":
            continue
        try:
            sc = Sidecar.read(json_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Skipping unreadable sidecar %s: %s", json_path, exc)
            continue
        media_path = json_path.with_name(sc.file.name)
        target_dir = cat_dir / sc.file.media_type
        target_dir.mkdir(parents=True, exist_ok=True)
        new_json = target_dir / json_path.name
        new_media = target_dir / media_path.name
        if media_path.is_file():
            os.replace(media_path, new_media)
        else:
            logger.warning(
                "Sidecar %s has no matching media file (%s missing)", json_path, media_path
            )
        os.replace(json_path, new_json)
        moved += 1
        if index is not None:
            entry = index.get(sc.source.sha256)
            if entry is not None:
                entry.relpath = new_media.relative_to(output_dir).as_posix()
    _cleanup_empty_media_subdirs(cat_dir)
    return moved


def upgrade_sidecar_schema(json_path: Path, video_extensions: frozenset[str] | set[str]) -> bool:
    """Rewrite a sidecar whose `schema_version` is older than current (atomic write), filling
    in the newer fields from the actual media file on disk. `media_type` is (re)detected from
    the stored file's extension (an animated `.gif`/`.webp` -> `gif`, `video_extensions` ->
    `video`, else `image`) rather than trusted from whatever the old sidecar happened to say.
    Returns True if the file was rewritten, False if it was already current (or unreadable).
    """
    try:
        sc = Sidecar.read(json_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("Skipping unreadable sidecar %s: %s", json_path, exc)
        return False
    if sc.schema_version >= SCHEMA_VERSION:
        return False

    media_path = json_path.with_name(sc.file.name)
    media_type = "image"
    if media_path.is_file():
        try:
            media_type = detect_media_type(media_path, video_extensions)
        except Exception as exc:  # noqa: BLE001 - a stale sidecar must never block migration
            logger.warning("Could not detect media type for %s: %s", media_path, exc)
    sc.file.media_type = media_type
    sc.schema_version = SCHEMA_VERSION
    sc.write(json_path)
    return True


def _upgrade_all_sidecars(output_dir: Path, video_extensions: frozenset[str] | set[str]) -> int:
    upgraded = 0
    for json_path in sorted(output_dir.rglob("*.json")):
        if json_path.name == "categories.json":
            continue
        try:
            rel_parts = json_path.relative_to(output_dir).parts
        except ValueError:
            continue
        if any(part == INDEX_DIR_NAME for part in rel_parts):
            continue
        if upgrade_sidecar_schema(json_path, video_extensions):
            upgraded += 1
    return upgraded


def migrate_collection(output_dir: Path, settings, console: Console | None = None) -> RunSummary:
    """Normalise an existing collection to the v2 `<category>/<image|gif|video>/` layout.

    No LLM calls: purely a filesystem/index/categories.json normalisation, safe to run
    repeatedly (idempotent). Also registers the pinned NSFW category (id 0, adopting whatever
    folder already exists on disk) when `categorize.nsfw.enabled`, and rewrites any sidecar
    still at `schema_version < 2` in place.
    """
    console = console or Console()
    output_dir = Path(output_dir)
    started_at = utcnow_iso()
    start_clock = time.monotonic()

    index = Index.load_or_rebuild(output_dir)
    cf = load_categories_file(output_dir)

    categories_created: list[CreatedCategory] = []
    nsfw_cfg = settings.categorize.nsfw
    if nsfw_cfg.enabled:
        cf, nsfw_cat, nsfw_was_created = ensure_nsfw_category(output_dir, cf, nsfw_cfg)
        if nsfw_was_created:
            categories_created.append(
                CreatedCategory(id=nsfw_cat.id, name=nsfw_cat.name, folder=nsfw_cat.folder)
            )

    moved = 0
    if cf is not None:
        for category in cf.categories:
            moved += normalize_category_layout(output_dir, category, index)
        if cf.layout_version != LAYOUT_VERSION:
            cf.layout_version = LAYOUT_VERSION
            cf.write(output_dir / "categories.json")

    video_extensions = frozenset(e.lower() for e in settings.processing.video_extensions)
    upgraded = _upgrade_all_sidecars(output_dir, video_extensions)

    index.flush()

    console.print(f"[bold]migrate[/]: {moved} pair(s) moved into the v2 layout")
    console.print(f"Sidecars upgraded: {upgraded}")
    if categories_created:
        folders = ", ".join(c.folder for c in categories_created)
        console.print(f"Created categories: {folders}")

    summary = RunSummary(
        run_id=new_run_id(),
        command="migrate",
        started_at=started_at,
        finished_at=utcnow_iso(),
        duration_s=round(time.monotonic() - start_clock, 3),
        moved=moved,
        sidecars_upgraded=upgraded,
        categories_created=categories_created,
        output_path=str(output_dir),
    )
    index.write_run_summary(summary)
    return summary
