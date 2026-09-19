"""The `categorize` pipeline: digest, discovery, assignment, and apply."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import ValidationError
from rich.console import Console

from atomik_meme.categories import (
    LAYOUT_VERSION,
    ensure_nsfw_category,
    is_pinned_id,
    plan_nsfw_category,
    sort_categories,
)
from atomik_meme.config import DEFAULT_TEXT_NUM_PREDICT
from atomik_meme.index import INDEX_DIR_NAME, Index
from atomik_meme.naming import slugify
from atomik_meme.ollama_client import ModelMissing, OllamaClient, OllamaError
from atomik_meme.prompts import (
    ASSIGN_SYSTEM,
    DISCOVERY_SYSTEM,
    assign_user_prompt,
    discovery_user_prompt,
)
from atomik_meme.schema import (
    SCHEMA_VERSION,
    CategoriesFile,
    Category,
    CategoryAssignment,
    CreatedCategory,
    IndexEntry,
    RunSummary,
    Sidecar,
    build_assignment_output_model,
    build_discovery_output_model,
    clip_to_limits,
    new_run_id,
    to_ollama_schema,
    utcnow_iso,
)

logger = logging.getLogger(__name__)

_NN_FOLDER_RE = re.compile(r"^\d{2}-")


class CategorizeError(Exception):
    """Raised when discovery output keeps failing validation after all retries."""


def _preflight_text(client: OllamaClient, model_name: str, ollama_cfg) -> str:
    version = client.version()
    if not client.has_model(model_name):
        if ollama_cfg.auto_pull:
            client.pull(model_name)
        else:
            raise ModelMissing(
                f"Model '{model_name}' is not installed on {client.host}.\n"
                f"Run: ollama pull {model_name}\n"
                f"(OLLAMA_HOST={client.host})"
            )
    return version


def _slugify_category(name: str, max_len: int = 30) -> str:
    return slugify(name, max_len=max_len, drop_leading_articles=False)


def _iter_all_sidecars(output_dir: Path) -> list[tuple[Path, Sidecar]]:
    output_dir = Path(output_dir)
    results: list[tuple[Path, Sidecar]] = []
    if not output_dir.is_dir():
        return results
    for json_path in sorted(output_dir.rglob("*.json")):
        try:
            rel_parts = json_path.relative_to(output_dir).parts
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


def _build_digest(sidecars: list[tuple[Path, Sidecar]], top_tags_n: int) -> tuple[list[dict], dict]:
    records: list[dict] = []
    tag_counts: Counter = Counter()
    topic_counts: Counter = Counter()
    type_counts: Counter = Counter()
    tone_counts: Counter = Counter()
    for _path, sc in sidecars:
        a = sc.analysis
        records.append(
            {
                "id": sc.id,
                "title": a.title,
                "tags": a.tags[:8],
                "meme_type": a.meme_type,
                "tone": a.tone[:3],
                "topics": a.topics[:3],
                "ocr": a.ocr_text[:100],
            }
        )
        tag_counts.update(a.tags)
        topic_counts.update(a.topics)
        type_counts.update([a.meme_type])
        tone_counts.update(a.tone)

    stats = {
        "total": len(records),
        "top_tags": tag_counts.most_common(top_tags_n),
        "topics": topic_counts.most_common(30),
        "meme_type_distribution": type_counts.most_common(),
        "tone_distribution": tone_counts.most_common(20),
    }
    return records, stats


def _select_discovery_sample(records: list[dict], sample_size: int) -> list[dict]:
    """Deterministic sample: seeded by sorted ids, stratified over the top-30 tags."""
    by_id = {r["id"]: r for r in records}
    if len(records) <= sample_size:
        return [by_id[i] for i in sorted(by_id)]

    ids_sorted = sorted(by_id)
    seed = int(hashlib.sha256("".join(ids_sorted).encode("utf-8")).hexdigest(), 16)
    rng = random.Random(seed)

    tag_counts: Counter = Counter()
    for r in records:
        tag_counts.update(r["tags"])
    top_tags = [t for t, _ in tag_counts.most_common(30)]

    selected: list[str] = []
    selected_set: set[str] = set()
    for tag in top_tags:
        if len(selected) >= sample_size:
            break
        candidates = sorted(
            r["id"] for r in records if tag in r["tags"] and r["id"] not in selected_set
        )
        if candidates:
            chosen = rng.choice(candidates)
            selected.append(chosen)
            selected_set.add(chosen)

    remaining_pool = sorted(set(by_id) - selected_set)
    rng.shuffle(remaining_pool)
    for rid in remaining_pool:
        if len(selected) >= sample_size:
            break
        selected.append(rid)
        selected_set.add(rid)

    return [by_id[i] for i in sorted(selected)]


def _run_discovery(
    client,
    model_cfg,
    stats: dict,
    sample: list[dict],
    n_categories: int,
    others_name: str,
    max_retries: int,
    pinned_names: list[str] | None = None,
) -> list[dict]:
    # Built here (not a module-level constant) because `n_categories` comes from
    # `categorize.category_count`, a config value - see build_discovery_output_model.
    discovery_model = build_discovery_output_model(n_categories)
    discovery_schema = to_ollama_schema(discovery_model)

    messages: list[dict] = [
        {"role": "system", "content": DISCOVERY_SYSTEM},
        {
            "role": "user",
            "content": discovery_user_prompt(stats, sample, n_categories, pinned_names or []),
        },
    ]
    options = {"num_ctx": model_cfg.num_ctx, "temperature": model_cfg.temperature}
    options["num_predict"] = model_cfg.num_predict or DEFAULT_TEXT_NUM_PREDICT
    total_attempts = max(1, max_retries + 1)
    last_problem = ""

    for attempt in range(1, total_attempts + 1):
        raw = client.chat_structured(
            model=model_cfg.name,
            messages=messages,
            schema=discovery_schema,
            options=options,
            think=model_cfg.think,
        )
        problems: list[str] = []
        try:
            parsed = discovery_model.model_validate(clip_to_limits(raw, discovery_model))
        except ValidationError as exc:
            problems.append(f"output did not match the schema: {exc}")
            parsed = None

        if parsed is not None:
            cats = parsed.categories
            if len(cats) != n_categories:
                problems.append(f"expected exactly {n_categories} categories, got {len(cats)}")
            slugs = [_slugify_category(c.name) for c in cats]
            if len(set(slugs)) != len(slugs):
                problems.append("category names must be unique (after slugifying)")
            for c in cats:
                if not c.name.strip():
                    problems.append("a category name was empty")
                elif c.name.strip().lower() == others_name.lower():
                    problems.append(
                        f"'{others_name}' is reserved and added automatically; do not include it"
                    )

        if not problems:
            return [c.model_dump() for c in parsed.categories]

        last_problem = "; ".join(problems)
        logger.info("Discovery attempt %d/%d invalid: %s", attempt, total_attempts, last_problem)
        if attempt < total_attempts:
            messages.append({"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"That was invalid: {last_problem}. Please answer again, following the "
                        "rules exactly."
                    ),
                }
            )

    raise CategorizeError(
        f"Discovery failed validation after {total_attempts} attempt(s): {last_problem}"
    )


def _call_assign_batch(
    client,
    model_cfg,
    categories_prompt: list[dict],
    batch: list[dict],
    assignment_model,
    assign_schema: dict,
) -> dict[str, object]:
    messages = [
        {"role": "system", "content": ASSIGN_SYSTEM},
        {"role": "user", "content": assign_user_prompt(categories_prompt, batch)},
    ]
    options = {"num_ctx": model_cfg.num_ctx, "temperature": model_cfg.temperature}
    options["num_predict"] = model_cfg.num_predict or DEFAULT_TEXT_NUM_PREDICT
    raw = client.chat_structured(
        model=model_cfg.name,
        messages=messages,
        schema=assign_schema,
        options=options,
        think=model_cfg.think,
    )
    parsed = assignment_model.model_validate(clip_to_limits(raw, assignment_model))
    return {item.id: item for item in parsed.assignments}


def _finalize_assignment(item, min_confidence: float, others_id: int, valid_ids: set[int]) -> dict:
    cat_id = item.category_id
    confidence = item.confidence
    suggested = None
    if cat_id not in valid_ids:
        # Belt and braces: the schema already constrains category_id to 1..category_count,
        # but grammar enforcement on the server side has been observed to be imperfect -
        # never trust it blindly (a raw, unvalidated int reaching the apply step is how a
        # KeyError there used to crash the whole categorize run).
        logger.warning(
            "Assignment for %s named unknown category_id=%r; routing to others", item.id, cat_id
        )
        suggested = cat_id
        cat_id = others_id
    elif confidence < min_confidence:
        suggested = cat_id
        cat_id = others_id
    return {
        "category_id": cat_id,
        "confidence": confidence,
        "reason": item.reason,
        "suggested": suggested,
        "source": "llm",
    }


def _assign_records(
    client,
    model_cfg,
    categories_raw: list[dict],
    records: list[dict],
    batch_size: int,
    min_confidence: float,
    others_id: int,
    workers: int,
) -> dict[str, dict]:
    if not records:
        return {}

    valid_ids = {c["id"] for c in categories_raw}
    assignment_model = build_assignment_output_model(others_id)
    assign_schema = to_ollama_schema(assignment_model)

    records_sorted = sorted(records, key=lambda r: r["id"])
    batches = [
        records_sorted[i : i + batch_size] for i in range(0, len(records_sorted), batch_size)
    ]
    categories_prompt = [
        {
            "id": c["id"],
            "name": c["name"],
            "description": c.get("description", ""),
            "keywords": c.get("keywords", []),
        }
        for c in categories_raw
    ]

    def process_batch(batch: list[dict]) -> dict[str, dict]:
        batch_ids = [r["id"] for r in batch]
        try:
            assignments = _call_assign_batch(
                client, model_cfg, categories_prompt, batch, assignment_model, assign_schema
            )
        except (ValidationError, OllamaError) as exc:
            logger.warning("Assignment batch of %d failed: %s", len(batch), exc)
            assignments = {}

        local: dict[str, dict] = {}
        missing_ids = []
        for rid in batch_ids:
            item = assignments.get(rid)
            if item is None:
                missing_ids.append(rid)
                continue
            local[rid] = _finalize_assignment(item, min_confidence, others_id, valid_ids)

        for rid in missing_ids:
            record = next(r for r in batch if r["id"] == rid)
            item = None
            try:
                singleton = _call_assign_batch(
                    client,
                    model_cfg,
                    categories_prompt,
                    [record],
                    assignment_model,
                    assign_schema,
                )
                item = singleton.get(rid)
            except (ValidationError, OllamaError) as exc:
                logger.warning("Singleton assignment retry failed for %s: %s", rid, exc)
            if item is None:
                local[rid] = {
                    "category_id": others_id,
                    "confidence": 0.0,
                    "reason": "no valid assignment returned",
                    "suggested": None,
                }
            else:
                local[rid] = _finalize_assignment(item, min_confidence, others_id, valid_ids)
        return local

    results: dict[str, dict] = {}
    if workers <= 1 or len(batches) <= 1:
        for batch in batches:
            results.update(process_batch(batch))
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for local in executor.map(process_batch, batches):
                results.update(local)
    return results


def _finalize_categories(
    categories_raw: list[dict], counts: Counter, others_id: int
) -> tuple[list[Category], dict[int, int]]:
    non_others = [c for c in categories_raw if c["id"] != others_id]
    non_others_sorted = sorted(non_others, key=lambda c: -counts.get(c["id"], 0))
    others = next(
        (c for c in categories_raw if c["id"] == others_id),
        {"name": "others", "description": "", "keywords": []},
    )

    id_map: dict[int, int] = {}
    final: list[Category] = []
    for new_id, c in enumerate(non_others_sorted, start=1):
        id_map[c["id"]] = new_id
        folder = f"{new_id:02d}-{_slugify_category(c['name'])}"
        final.append(
            Category(
                id=new_id,
                name=c["name"],
                folder=folder,
                description=c.get("description", ""),
                keywords=c.get("keywords", []),
                count=counts.get(c["id"], 0),
            )
        )
    id_map[others.get("id", others_id)] = others_id
    folder = f"{others_id:02d}-{_slugify_category(others['name'])}"
    final.append(
        Category(
            id=others_id,
            name=others["name"],
            folder=folder,
            description=others.get("description", ""),
            keywords=others.get("keywords", []),
            count=counts.get(others_id, 0),
        )
    )
    return final, id_map


def _cleanup_empty_category_folders(output_dir: Path) -> None:
    for entry in sorted(output_dir.iterdir()):
        if not entry.is_dir():
            continue
        # Empty `<image|gif|video>/` subfolders first (v2 layout), then the category folder
        # itself if that leaves it with nothing left in it.
        for sub in ("image", "gif", "video"):
            subdir = entry / sub
            if subdir.is_dir():
                try:
                    if not any(subdir.iterdir()):
                        subdir.rmdir()
                except OSError:
                    pass
        if _NN_FOLDER_RE.match(entry.name):
            try:
                if not any(entry.iterdir()):
                    entry.rmdir()
            except OSError:
                pass


def _print_categorize_preview(
    console: Console, categories: list[Category], final_category: dict[str, dict], by_id: dict
) -> None:
    examples: dict[int, list[str]] = defaultdict(list)
    for rid in sorted(final_category):
        info = final_category[rid]
        examples[info["category_id"]].append(by_id[rid][1].analysis.title)

    console.print("[bold]categorize[/] (dry run) - proposed categories:")
    for cat in sorted(categories, key=lambda c: c.id):
        console.print(f"  {cat.id:2d}. {cat.folder:<30} count={cat.count}")
        for title in examples.get(cat.id, [])[:5]:
            console.print(f"        - {title}")


def categorize_collection(
    output_dir: Path,
    settings,
    profile: str = "fast",
    text_model_override: str | None = None,
    rediscover: bool = False,
    dry_run: bool = False,
    workers: int | None = None,
    console: Console | None = None,
) -> RunSummary:
    console = console or Console()
    output_dir = Path(output_dir)
    started_at = utcnow_iso()
    start_clock = time.monotonic()
    run_id = new_run_id()

    model_cfg = settings.text_model(profile).model_copy(deep=True)
    if text_model_override:
        model_cfg.name = text_model_override
    n_workers = workers if workers is not None else settings.categorize.workers
    if n_workers < 1:
        raise ValueError(f"workers must be >= 1, got {n_workers}")

    all_sidecars = _iter_all_sidecars(output_dir)
    by_id = {sc.id: (path, sc) for path, sc in all_sidecars}

    # Pinned categories (id 0 NSFW, ids 11+ user) are excluded from the digest, discovery,
    # and assignment entirely, and their members are never touched by the apply step below
    # A meme still sitting uncategorised whose analysis flagged nsfw is
    # auto-routed to the pinned NSFW category here too (process-time already handles most of
    # these; this covers memes processed before the rule was enabled, or without a hint).
    nsfw_cfg = settings.categorize.nsfw
    pinned_sidecars = [
        (p, sc)
        for p, sc in all_sidecars
        if sc.category is not None and is_pinned_id(sc.category.id)
    ]
    nsfw_ids: set[str] = set()
    if nsfw_cfg.enabled:
        nsfw_ids = {sc.id for _p, sc in all_sidecars if sc.category is None and sc.analysis.nsfw}
    excluded_ids = {sc.id for _p, sc in pinned_sidecars} | nsfw_ids
    digest_sidecars = [(p, sc) for p, sc in all_sidecars if sc.id not in excluded_ids]

    records, stats = _build_digest(digest_sidecars, settings.categorize.top_tags_in_digest)

    summary = RunSummary(
        run_id=run_id,
        command="categorize",
        started_at=started_at,
        finished_at=started_at,
        duration_s=0.0,
        profile=profile,
        text_model=model_cfg.name,
        output_path=str(output_dir),
    )

    if not records:
        console.print("No sidecars found under the output directory; nothing to categorize.")
        summary.finished_at = utcnow_iso()
        summary.duration_s = round(time.monotonic() - start_clock, 3)
        return summary

    categories_path = output_dir / "categories.json"
    existing_categories_file = None
    if categories_path.is_file():
        try:
            existing_categories_file = CategoriesFile.read(categories_path)
        except (json.JSONDecodeError, OSError, ValueError):
            existing_categories_file = None

    # Ensure the pinned NSFW category is registered up front, same as `process`/`migrate`,
    # so a categories.json containing only ids 1..10 (e.g. from a collection that has never had
    # an nsfw meme routed yet) still exposes id 0 for anything that looks it up. Skipped during
    # --dry-run, which must never write to disk.
    categories_created: list[CreatedCategory] = []
    if nsfw_cfg.enabled and not dry_run:
        existing_categories_file, nsfw_cat, nsfw_was_created = ensure_nsfw_category(
            output_dir, existing_categories_file, nsfw_cfg
        )
        if nsfw_was_created:
            categories_created.append(
                CreatedCategory(id=nsfw_cat.id, name=nsfw_cat.name, folder=nsfw_cat.folder)
            )

    others_name = settings.categorize.others_name
    others_id = settings.categorize.category_count

    # A categories.json containing ONLY pinned categories (created by process-time hints/nsfw
    # routing, before any real categorize run) must not be mistaken for "already discovered" -
    # assign-only mode requires the 1..category_count discovered set to actually exist.
    has_discovered_categories = existing_categories_file is not None and any(
        1 <= c.id <= others_id for c in existing_categories_file.categories
    )
    assign_only = has_discovered_categories and not rediscover

    # Discovery/assignment need the model whether or not this is a dry run - only the final
    # apply step (moves, sidecar/category writes, categories.json, index flush) is skipped for
    # --dry-run, so the client is always built (a --dry-run that never built one used to crash
    # with an AttributeError the moment discovery tried to use `client=None`).
    client = OllamaClient(
        host=settings.ollama.host,
        timeout_s=settings.ollama.timeout_s,
        retries=settings.ollama.retries,
        keep_alive=settings.ollama.keep_alive,
    )
    _preflight_text(client, model_cfg.name, settings.ollama)

    pinned_names = sorted(
        {c.name for c in (existing_categories_file.categories if existing_categories_file else [])}
    )

    pinned_raw: list[dict] = [
        c.model_dump()
        for c in (existing_categories_file.categories if existing_categories_file else [])
        if is_pinned_id(c.id)
    ]

    try:
        current_assignment: dict[str, int] = {}
        if assign_only:
            all_categories_raw = [c.model_dump() for c in existing_categories_file.categories]
            categories_raw = [c for c in all_categories_raw if not is_pinned_id(c["id"])]
            category_by_id = {c["id"]: c for c in categories_raw}
            to_assign = []
            for rec in records:
                path, sc = by_id[rec["id"]]
                if sc.category is not None and sc.category.id in category_by_id:
                    current_assignment[rec["id"]] = sc.category.id
                    continue
                to_assign.append(rec)
        else:
            sample = _select_discovery_sample(records, settings.categorize.discovery_sample_size)
            n_categories = settings.categorize.category_count - 1
            categories_raw = _run_discovery(
                client,
                model_cfg,
                stats,
                sample,
                n_categories,
                others_name,
                settings.categorize.max_retries,
                pinned_names,
            )
            for i, c in enumerate(categories_raw, start=1):
                c["id"] = i
            categories_raw.append(
                {
                    "id": others_id,
                    "name": others_name,
                    "description": "Uncertain or miscellaneous memes.",
                    "keywords": [],
                }
            )
            to_assign = records

        assignment_results = _assign_records(
            client,
            model_cfg,
            categories_raw,
            to_assign,
            settings.categorize.assign_batch_size,
            settings.categorize.min_confidence,
            others_id,
            n_workers,
        )

        final_category: dict[str, dict] = {
            rid: {
                "category_id": cat_id,
                "confidence": None,
                "reason": "",
                "suggested": None,
                "source": None,  # None -> apply step keeps the sidecar's existing source
            }
            for rid, cat_id in current_assignment.items()
        }
        final_category.update(assignment_results)
        for rid in nsfw_ids:
            final_category[rid] = {
                "category_id": 0,
                "confidence": 1.0,
                "reason": "",
                "suggested": None,
                "source": "rule",
            }

        def _pinned_cat_id(cid: int) -> bool:
            return is_pinned_id(cid)

        counts: Counter = Counter(
            info["category_id"]
            for info in final_category.values()
            if not _pinned_cat_id(info["category_id"])
        )

        if assign_only:
            final_categories = [
                Category(
                    id=c["id"],
                    name=c["name"],
                    folder=c["folder"],
                    description=c.get("description", ""),
                    keywords=c.get("keywords", []),
                    count=counts.get(c["id"], 0),
                )
                for c in categories_raw
            ]
        else:
            final_categories, id_map = _finalize_categories(categories_raw, counts, others_id)
            for info in final_category.values():
                if not _pinned_cat_id(info["category_id"]):
                    info["category_id"] = id_map.get(info["category_id"], info["category_id"])
            counts = Counter(
                info["category_id"]
                for info in final_category.values()
                if not _pinned_cat_id(info["category_id"])
            )
            for cat in final_categories:
                cat.count = counts.get(cat.id, 0)

        # Merge in pinned categories (untouched by discovery/assignment above) so they survive
        # in categories.json, with their `count` recomputed from actual pinned members: prior
        # ones already on disk, plus any nsfw memes routed here for the first time this run.
        pinned_final = [Category.model_validate(c) for c in pinned_raw]
        if nsfw_cfg.enabled and nsfw_ids and not any(c.id == 0 for c in pinned_final):
            pinned_final.append(plan_nsfw_category(existing_categories_file, nsfw_cfg))
        pinned_prior_counts: Counter = Counter(sc.category.id for _p, sc in pinned_sidecars)
        pinned_new_counts: Counter = Counter(
            info["category_id"]
            for info in final_category.values()
            if _pinned_cat_id(info["category_id"])
        )
        for cat in pinned_final:
            cat.count = pinned_prior_counts.get(cat.id, 0) + pinned_new_counts.get(cat.id, 0)

        all_final_categories = sort_categories([*pinned_final, *final_categories])

        if dry_run:
            _print_categorize_preview(console, all_final_categories, final_category, by_id)
            summary.categorized = len(final_category)
            summary.finished_at = utcnow_iso()
            summary.duration_s = round(time.monotonic() - start_clock, 3)
            return summary

        index = Index.load_or_rebuild(output_dir)
        categories_by_id = {c.id: c for c in all_final_categories}
        others_category = categories_by_id.get(others_id)
        applied = 0

        for rid, info in final_category.items():
            try:
                path, sc = by_id[rid]
                cat = categories_by_id.get(info["category_id"]) or others_category
                if cat is None:
                    # Only reachable if `others` itself is somehow missing from
                    # final_categories - defend anyway rather than KeyError the whole run.
                    raise CategorizeError(f"No category (not even others) for id {rid}")

                media_type = sc.file.media_type
                target_dir = output_dir / cat.folder / media_type
                is_rule_forced = rid in nsfw_ids
                newly_assigned = is_rule_forced or rid in assignment_results
                needs_move = path.parent != target_dir
                if not newly_assigned and not needs_move:
                    applied += 1
                    continue

                target_dir.mkdir(parents=True, exist_ok=True)
                stem = path.stem
                src_media = path.with_name(sc.file.name)
                if needs_move:
                    dst_media = target_dir / src_media.name
                    dst_json = target_dir / f"{stem}.json"
                    if src_media.is_file():
                        os.replace(src_media, dst_media)
                    else:
                        logger.warning(
                            "Sidecar %s has no matching media file (%s missing); moving the "
                            "sidecar anyway",
                            path,
                            src_media,
                        )
                    os.replace(path, dst_json)
                else:
                    dst_media, dst_json = src_media, path

                suggested = info.get("suggested")
                if is_rule_forced:
                    confidence, assigned_at = 1.0, utcnow_iso()
                    model_used, run_id_used, source_used = "", run_id, "rule"
                elif newly_assigned:
                    confidence = info["confidence"]
                    assigned_at, model_used, run_id_used = utcnow_iso(), model_cfg.name, run_id
                    source_used = info.get("source") or "llm"
                else:
                    confidence = (
                        info["confidence"]
                        if info["confidence"] is not None
                        else (sc.category.confidence if sc.category else 1.0)
                    )
                    assigned_at = sc.category.assigned_at if sc.category else utcnow_iso()
                    model_used = sc.category.model if sc.category else model_cfg.name
                    run_id_used = sc.category.run_id if sc.category else run_id
                    source_used = sc.category.source if sc.category else "llm"

                sc.category = CategoryAssignment(
                    id=cat.id,
                    name=cat.name,
                    confidence=confidence,
                    assigned_at=assigned_at,
                    model=model_used,
                    run_id=run_id_used,
                    suggested=str(suggested) if suggested is not None else None,
                    source=source_used,
                )
                # A sidecar this touches is rewritten anyway - opportunistically bring an old
                # v1 one up to the current schema_version rather than leaving it stale (`migrate`
                # is still responsible for upgrading everything this step doesn't happen to touch).
                if sc.schema_version < SCHEMA_VERSION:
                    sc.schema_version = SCHEMA_VERSION
                sc.write(dst_json)

                relpath = dst_media.relative_to(output_dir).as_posix()
                idx_entry = index.get(sc.source.sha256)
                if idx_entry:
                    idx_entry.relpath = relpath
                    idx_entry.category_id = cat.id
                else:
                    index.put(
                        IndexEntry(
                            id=sc.id,
                            sha256=sc.source.sha256,
                            stem=stem,
                            relpath=relpath,
                            status="done",
                            category_id=cat.id,
                            processed_at=sc.processing.processed_at,
                        )
                    )
                applied += 1
            except Exception as exc:  # noqa: BLE001 - one bad item must not abort the run
                logger.warning("Failed to apply category for %s: %s", rid, exc)
                source_path = str(by_id[rid][0]) if rid in by_id else rid
                index.record_failure(source_path, "move", str(exc))

        # Flush and write categories.json for whatever succeeded, even if some items failed.
        index.flush()
        _cleanup_empty_category_folders(output_dir)

        categories_file = CategoriesFile(
            generated_at=utcnow_iso(),
            model=model_cfg.name,
            profile=profile,
            run_id=run_id,
            collection_size=len(all_sidecars),
            layout_version=LAYOUT_VERSION,
            categories=all_final_categories,
        )
        categories_file.write(categories_path)

        summary.categorized = applied
        summary.categories_created = categories_created
        summary.failed = len(index.failures)
        summary.failures = index.failures
        summary.finished_at = utcnow_iso()
        summary.duration_s = round(time.monotonic() - start_clock, 3)
        if index.failures:
            index.write_failures()
        index.write_run_summary(summary)
        return summary
    finally:
        client.close()
