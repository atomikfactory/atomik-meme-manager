"""The `process` pipeline: discover -> hash -> decode -> prepare -> analyse -> name -> write.

Hashing and duplicate detection happen in the main thread before dispatch (so
they are inherently thread-safe); the thread pool only does decode/prepare/
analyse (the expensive, network-bound part). Naming, image/sidecar writing,
and index updates happen back in the main thread as results complete, which
avoids any locking around the naming-collision and index state.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import signal
import threading
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

from atomik_meme import __version__, media
from atomik_meme.analyzer import AnalysisError, analyze_image
from atomik_meme.categories import (
    category_media_dir,
    ensure_nsfw_category,
    load_categories_file,
    resolve_hints,
)
from atomik_meme.images import (
    ImageDecodeError,
    LoadedImage,
    detect_media_type,
    discover_images,
    load_image,
    prepare_for_model,
    prepare_frames_for_model,
    probe_gif,
    sample_gif_frames,
    sha256_file,
    should_copy_verbatim,
    write_output_image,
    write_verbatim_media,
)
from atomik_meme.index import Index
from atomik_meme.naming import resolve_collision, slugify
from atomik_meme.ollama_client import ModelMissing, OllamaClient, OllamaError
from atomik_meme.prompts import PROMPT_VERSION
from atomik_meme.schema import (
    Analysis,
    Category,
    CategoryAssignment,
    CreatedCategory,
    FileInfo,
    ImageSent,
    IndexEntry,
    ProcessingInfo,
    RunSummary,
    Sidecar,
    SourceInfo,
    new_run_id,
    utcnow_iso,
)

logger = logging.getLogger(__name__)

INDEX_FLUSH_EVERY = 10


@dataclass
class _Task:
    source_path: Path
    sha256: str
    hint_category: Category | None = None


@dataclass
class _WorkResult:
    task: _Task
    ok: bool
    stage: str = ""
    message: str = ""
    analysis: Analysis | None = None
    media_type: str = "image"
    loaded: LoadedImage | None = None  # image pipeline only
    width: int = 0  # gif/video only (image path reads dims off `loaded`)
    height: int = 0
    media_duration_s: float | None = None  # gif/video length in seconds
    fps: float | None = None
    frame_count: int | None = None
    has_audio: bool | None = None
    over_duration_limit: bool = False
    tiles: int = 0  # tall-image tiling count (image pipeline only)
    frames_sent: int = 1  # gif/video sampled-frame count
    elapsed_s: float = 0.0
    attempts: int = 1
    ocr_ran: bool = False


def _analyze_task(
    task: _Task,
    model_cfg,
    client,
    image_cfg,
    media_cfg,
    video_extensions: frozenset[str],
    timeout_override: float | None,
    ocr_pass: str,
) -> _WorkResult:
    t0 = time.monotonic()
    media_type = detect_media_type(task.source_path, video_extensions)

    loaded: LoadedImage | None = None
    width = height = 0
    media_duration_s: float | None = None
    fps: float | None = None
    frame_count: int | None = None
    has_audio: bool | None = None
    over_duration_limit = False
    tiles_count = 0
    frames_sent = 1

    try:
        if media_type == "video":
            try:
                frames, probe = media.sample_video_frames(
                    task.source_path,
                    media_cfg.frames_per_video,
                    media_cfg.ffmpeg_path,
                    media_cfg.ffprobe_path,
                )
            except media.FFmpegNotFoundError as exc:
                return _WorkResult(
                    task=task,
                    ok=False,
                    stage="decode",
                    message=f"{exc} (check media.ffmpeg_path/media.ffprobe_path in config)",
                )
            except media.VideoDecodeError as exc:
                return _WorkResult(task=task, ok=False, stage="decode", message=str(exc))
            media_duration_s = probe.duration_s
            fps = probe.fps
            frame_count = probe.frame_count
            has_audio = probe.has_audio
            over_duration_limit = media_duration_s > media_cfg.max_video_duration_s
            if over_duration_limit:
                logger.warning(
                    "Video exceeds media.max_video_duration_s (%.1fs > %.1fs): %s",
                    media_duration_s,
                    media_cfg.max_video_duration_s,
                    task.source_path,
                )
            if frames:
                width, height = frames[0].size
            tiles_bytes = prepare_frames_for_model(
                frames, image_cfg, media_cfg.frame_max_total_pixels
            )
            frames_sent = len(tiles_bytes)
        elif media_type == "gif":
            frames = sample_gif_frames(task.source_path, media_cfg.frames_per_gif)
            if not frames:
                return _WorkResult(
                    task=task, ok=False, stage="decode", message="No frames decoded from gif"
                )
            width, height = frames[0].size
            frame_count, media_duration_s, fps = probe_gif(task.source_path)
            tiles_bytes = prepare_frames_for_model(
                frames, image_cfg, media_cfg.frame_max_total_pixels
            )
            frames_sent = len(tiles_bytes)
        else:
            loaded = load_image(task.source_path)
            tiles_bytes = prepare_for_model(loaded.image, image_cfg)
            tiles_count = len(tiles_bytes)
    except ImageDecodeError as exc:
        return _WorkResult(task=task, ok=False, stage="decode", message=str(exc))
    except Exception as exc:  # noqa: BLE001 - any other prepare-stage failure
        return _WorkResult(
            task=task, ok=False, stage="prepare", message=f"{type(exc).__name__}: {exc}"
        )

    tiles_b64 = [base64.b64encode(b).decode("ascii") for b in tiles_bytes]

    try:
        analysis, attempts, ocr_ran = analyze_image(
            client,
            model_cfg,
            tiles_b64,
            timeout_override=timeout_override,
            ocr_pass=ocr_pass,
            media_kind=media_type,
            duration_s=media_duration_s,
        )
    except AnalysisError as exc:
        return _WorkResult(task=task, ok=False, stage="analyze", message=str(exc))
    except OllamaError as exc:
        return _WorkResult(task=task, ok=False, stage="ollama", message=str(exc))

    return _WorkResult(
        task=task,
        ok=True,
        analysis=analysis,
        media_type=media_type,
        loaded=loaded,
        width=width,
        height=height,
        media_duration_s=media_duration_s,
        fps=fps,
        frame_count=frame_count,
        has_audio=has_audio,
        over_duration_limit=over_duration_limit,
        tiles=tiles_count,
        frames_sent=frames_sent,
        elapsed_s=time.monotonic() - t0,
        attempts=attempts,
        ocr_ran=ocr_ran,
    )


def _preflight(client: OllamaClient, model_name: str, ollama_cfg, console: Console) -> str:
    """Verify the server is reachable and the model is present; return the server version."""
    version = client.version()
    if not client.has_model(model_name):
        if ollama_cfg.auto_pull:
            console.print(f"Model [bold]{model_name}[/] not found locally; pulling...")
            with Progress(console=console) as progress:
                bar = progress.add_task(f"Pulling {model_name}", total=None)

                def _cb(data: dict) -> None:
                    status = data.get("status", "")
                    progress.update(bar, description=f"Pulling {model_name}: {status}")

                client.pull(model_name, progress_cb=_cb)
        else:
            raise ModelMissing(
                f"Model '{model_name}' is not installed on {client.host}.\n"
                f"Run: ollama pull {model_name}\n"
                f"(OLLAMA_HOST={client.host})"
            )
    return version


def _dry_run_report(
    files: list[Path],
    output_dir: Path,
    force: bool,
    model_cfg,
    profile: str,
    console: Console,
    consume: bool = False,
) -> RunSummary:
    console.print(f"[bold]process[/] (dry run): {len(files)} file(s) found")
    console.print(f"Model: {model_cfg.name} (profile={profile})")

    index = Index.load_or_rebuild(output_dir) if output_dir.exists() else Index(output_dir)
    seen: set[str] = set()
    would_process = would_done = would_dup = would_remove = 0
    for path in files:
        sha = sha256_file(path)
        if not force and index.has(sha):
            would_done += 1
            status = "skip (already done)"
        elif sha in seen:
            would_dup += 1
            status = "skip (duplicate)"
        else:
            seen.add(sha)
            would_process += 1
            status = "process"
        if consume:
            # Optimistic preview: a dry run never decodes files, so it can't know which
            # would actually fail - this lists everything not already excluded as a candidate.
            status += " [+remove]"
            would_remove += 1
        console.print(f"  {status:<22} {path}")

    console.print(
        f"Would process: {would_process}   Would skip: {would_done + would_dup} "
        f"({would_done} already done, {would_dup} duplicates)"
    )
    if consume:
        console.print(f"Would remove from input: {would_remove}")
    now = utcnow_iso()
    return RunSummary(
        run_id=new_run_id(),
        command="process",
        started_at=now,
        finished_at=now,
        duration_s=0.0,
        profile=profile,
        vision_model=model_cfg.name,
        processed=0,
        skipped_done=would_done,
        skipped_duplicate=would_dup,
        consume=consume,
        removed_from_input=would_remove if consume else 0,
        output_path=str(output_dir),
    )


def _remove_empty_input_subfolders(input_dir: Path) -> list[Path]:
    """Remove input subfolders left empty by `--consume`. Never removes `input_dir` itself."""
    input_dir = Path(input_dir)
    removed: list[Path] = []
    dirs = sorted(
        (p for p in input_dir.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True
    )
    for d in dirs:
        try:
            if not any(d.iterdir()):
                d.rmdir()
                removed.append(d)
        except OSError:
            pass
    return removed


def _consume_sources(candidates: list[tuple[Path, str]], index: Index, output_dir: Path) -> int:
    """Delete each source whose sha256 has a verified, round-tripping output pair on disk."""
    removed = 0
    for src_path, sha in candidates:
        entry = index.get(sha)
        if entry is None:
            continue
        media_path = output_dir / entry.relpath
        json_path = media_path.with_suffix(".json")
        if not media_path.is_file() or not json_path.is_file():
            continue
        try:
            Sidecar.read(json_path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not src_path.is_file():
            continue
        try:
            os.remove(src_path)
            removed += 1
        except OSError as exc:
            logger.warning("Could not remove consumed source %s: %s", src_path, exc)
    return removed


def process_collection(
    input_dir: Path,
    output_dir: Path,
    settings,
    profile: str = "fast",
    vision_model_override: str | None = None,
    workers: int | None = None,
    force: bool = False,
    limit: int | None = None,
    dry_run: bool = False,
    console: Console | None = None,
    ocr_pass_override: str | None = None,
    category_hint: str | None = None,
    consume: bool | None = None,
) -> RunSummary:
    console = console or Console()
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)

    started_at = utcnow_iso()
    start_clock = time.monotonic()
    run_id = new_run_id()

    model_cfg = settings.vision_model(profile).model_copy(deep=True)
    if vision_model_override:
        model_cfg.name = vision_model_override
    ocr_pass = ocr_pass_override or settings.processing.ocr_pass
    n_workers = workers if workers is not None else settings.processing.workers
    if n_workers < 1:
        raise ValueError(f"workers must be >= 1, got {n_workers}")
    consume_effective = consume if consume is not None else settings.processing.remove_from_input
    video_extensions = frozenset(e.lower() for e in settings.processing.video_extensions)

    all_extensions = list(dict.fromkeys([*settings.processing.extensions, *video_extensions]))
    files = discover_images(input_dir, all_extensions, settings.processing.recursive)
    if limit is not None:
        files = files[:limit]

    if dry_run:
        return _dry_run_report(
            files, output_dir, force, model_cfg, profile, console, consume=consume_effective
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    index = Index.load_or_rebuild(output_dir)

    client = OllamaClient(
        host=settings.ollama.host,
        timeout_s=settings.ollama.timeout_s,
        retries=settings.ollama.retries,
        keep_alive=settings.ollama.keep_alive,
    )
    ollama_version = _preflight(client, model_cfg.name, settings.ollama, console)

    summary = RunSummary(
        run_id=run_id,
        command="process",
        started_at=started_at,
        finished_at=started_at,
        duration_s=0.0,
        profile=profile,
        vision_model=model_cfg.name,
        output_path=str(output_dir),
    )

    categories_file = load_categories_file(output_dir)

    # NSFW: ensure the pinned id-0 category is registered *before* any hint resolution,
    # so a hint like `--category nsfw` (or "NSFW", or "00 - NSFW") matches it instead of
    # creating a brand-new pinned user category. Adopts whatever folder already exists on disk
    # (the folder name comes from config either way, so this never creates a second folder).
    nsfw_cfg = settings.categorize.nsfw
    if nsfw_cfg.enabled:
        categories_file, nsfw_cat, nsfw_was_created = ensure_nsfw_category(
            output_dir, categories_file, nsfw_cfg
        )
        if nsfw_was_created:
            summary.categories_created.append(
                CreatedCategory(id=nsfw_cat.id, name=nsfw_cat.name, folder=nsfw_cat.folder)
            )

    # --- Group hints: CLI flag > first-level input subfolder > none. Unmatched hints
    # create a pinned user category (id >= 11) and write categories.json before dispatch.
    def _hint_name_for(path: Path) -> str | None:
        if category_hint:
            return category_hint
        try:
            rel_parts = path.relative_to(input_dir).parts
        except ValueError:
            rel_parts = ()
        return rel_parts[0] if len(rel_parts) > 1 else None

    hint_names_needed = [name for f in files if (name := _hint_name_for(f))]
    hint_by_name: dict[str, Category] = {}
    if hint_names_needed:
        categories_file, hint_by_name, created = resolve_hints(
            output_dir, categories_file, hint_names_needed
        )
        summary.categories_created.extend(
            CreatedCategory(id=c.id, name=c.name, folder=c.folder) for c in created
        )

    category_count_deltas: Counter[int] = Counter()

    seen_hashes: set[str] = set()
    tasks: list[_Task] = []
    consume_candidates: list[tuple[Path, str]] = []
    for path in files:
        sha = sha256_file(path)
        if not force and index.has(sha):
            summary.skipped_done += 1
            consume_candidates.append((path, sha))
            logger.info("Skip (already done): %s", path)
            continue
        if sha in seen_hashes:
            summary.skipped_duplicate += 1
            consume_candidates.append((path, sha))
            logger.info("Skip (duplicate content of another file this run): %s", path)
            continue
        seen_hashes.add(sha)
        hint_name = _hint_name_for(path)
        hint_category = hint_by_name.get(hint_name) if hint_name else None
        tasks.append(_Task(source_path=path, sha256=sha, hint_category=hint_category))

    stems_by_owner = index.all_stems()

    stop_event = threading.Event()
    previous_handler = None
    on_main_thread = threading.current_thread() is threading.main_thread()
    if on_main_thread:
        try:

            def _handle_sigint(signum, frame):  # noqa: ANN001
                console.print(
                    "\n[yellow]Interrupted - finishing in-flight work, then stopping...[/]"
                )
                stop_event.set()

            previous_handler = signal.signal(signal.SIGINT, _handle_sigint)
        except ValueError:
            on_main_thread = False

    processed_since_flush = 0

    def finalize_success(result: _WorkResult) -> None:
        nonlocal processed_since_flush, categories_file
        task = result.task
        id_ = task.sha256[:16]

        # `--force` reprocessing a sha already in the index: keep the pair in its existing
        # (possibly categorised) folder, carry its category over, and clean up the old files
        # once the new ones are safely written - never leave an orphaned old pair behind.
        old_entry = index.get(task.sha256)
        old_category = None
        old_media_path: Path | None = None
        old_json_path: Path | None = None
        target_dir = output_dir
        if old_entry is not None:
            old_media_path = output_dir / old_entry.relpath
            old_json_path = old_media_path.with_suffix(".json")
            target_dir = old_media_path.parent
            if stems_by_owner.get(old_entry.stem) == id_:
                del stems_by_owner[old_entry.stem]  # free it up for collision resolution below
            if old_json_path.is_file():
                try:
                    old_category = Sidecar.read(old_json_path).category
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    logger.warning("Could not read old sidecar %s: %s", old_json_path, exc)

        # Category routing: an explicit hint always wins; otherwise a `--force`
        # re-analysis keeps whatever category the meme already had; otherwise a fresh,
        # uncategorised meme flagged nsfw is auto-routed to the pinned NSFW category.
        chosen_category: Category | None = None
        chosen_source: str | None = None
        if task.hint_category is not None:
            chosen_category = task.hint_category
            chosen_source = "hint"
        elif old_category is None and nsfw_cfg.enabled and result.analysis.nsfw:
            categories_file, nsfw_cat, _created = ensure_nsfw_category(
                output_dir, categories_file, nsfw_cfg
            )
            chosen_category = nsfw_cat
            chosen_source = "rule"

        if chosen_category is not None:
            target_dir = category_media_dir(output_dir, chosen_category, result.media_type)
            category_count_deltas[chosen_category.id] += 1
            if chosen_source == "hint":
                summary.hinted += 1

        slug = slugify(
            result.analysis.title,
            settings.naming.max_slug_length,
            settings.naming.drop_leading_articles,
        )
        stem = resolve_collision(slug, id_, stems_by_owner)
        stems_by_owner[stem] = id_

        dest_ext = ".jpg" if result.media_type == "image" else task.source_path.suffix.lower()
        dest_media = target_dir / f"{stem}{dest_ext}"
        dest_json = target_dir / f"{stem}.json"

        if result.media_type == "image":
            file_info = write_output_image(
                task.source_path, result.loaded, dest_media, settings.image
            )
            converted = not should_copy_verbatim(result.loaded, settings.image)
            source_format = result.loaded.source_format
            source_width, source_height = result.loaded.source_width, result.loaded.source_height
            sent_width, sent_height = result.loaded.image.size
        else:
            size_bytes = write_verbatim_media(task.source_path, dest_media)
            converted = False
            source_format = dest_ext.lstrip(".")
            source_width, source_height = result.width, result.height
            sent_width, sent_height = result.width, result.height
            file_info = FileInfo(
                name=dest_media.name,
                width=result.width,
                height=result.height,
                bytes=size_bytes,
                format=source_format,
                animated=True,
                media_type=result.media_type,
                duration_s=result.media_duration_s,
                fps=result.fps,
                frame_count=result.frame_count,
                has_audio=result.has_audio,
                over_duration_limit=result.over_duration_limit,
            )

        source_info = SourceInfo(
            original_filename=task.source_path.name,
            original_path=task.source_path.resolve().as_posix(),
            sha256=task.sha256,
            format=source_format,
            width=source_width,
            height=source_height,
            bytes=task.source_path.stat().st_size,
            converted=converted,
        )
        processed_at = utcnow_iso()
        processing_info = ProcessingInfo(
            tool_version=__version__,
            profile=profile,
            vision_model=model_cfg.name,
            ollama_version=ollama_version,
            prompt_version=PROMPT_VERSION,
            processed_at=processed_at,
            duration_s=round(result.elapsed_s, 3),
            image_sent=ImageSent(
                width=sent_width,
                height=sent_height,
                tiles=result.tiles,
                frames=result.frames_sent,
            ),
            attempts=result.attempts,
            ocr_pass=ocr_pass if result.ocr_ran else None,
        )
        if chosen_category is not None:
            category_field = CategoryAssignment(
                id=chosen_category.id,
                name=chosen_category.name,
                confidence=1.0,
                assigned_at=processed_at,
                model="",
                run_id=run_id,
                source=chosen_source,
            )
        else:
            category_field = old_category
        sidecar = Sidecar(
            id=id_,
            file=file_info,
            source=source_info,
            analysis=result.analysis,
            category=category_field,
            processing=processing_info,
        )
        sidecar.write(dest_json)

        # Only now that the new pair is fully on disk do we remove the old one (a no-op when
        # the stem didn't change, since dest_* and old_*_path are then the same file).
        if old_media_path is not None and old_media_path != dest_media and old_media_path.is_file():
            old_media_path.unlink()
        if old_json_path is not None and old_json_path != dest_json and old_json_path.is_file():
            old_json_path.unlink()

        index.put(
            IndexEntry(
                id=id_,
                sha256=task.sha256,
                stem=stem,
                relpath=dest_media.relative_to(output_dir).as_posix(),
                status="done",
                category_id=category_field.id if category_field else None,
                processed_at=processed_at,
            )
        )
        consume_candidates.append((task.source_path, task.sha256))
        summary.processed += 1
        processed_since_flush += 1
        if processed_since_flush >= INDEX_FLUSH_EVERY:
            index.flush()
            processed_since_flush = 0

    def finalize_failure(result: _WorkResult) -> None:
        index.record_failure(str(result.task.source_path), result.stage, result.message)
        logger.warning(
            "Failed %s at stage=%s: %s", result.task.source_path, result.stage, result.message
        )

    try:
        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            future_map = {}
            for i, task in enumerate(tasks):
                if stop_event.is_set():
                    break
                # The first `n_workers` requests can each trigger a cold model load (they may
                # all start before any of them returns), so all of them - not just the very
                # first - get the doubled timeout.
                timeout_override = settings.ollama.timeout_s * 2 if i < n_workers else None
                fut = executor.submit(
                    _analyze_task,
                    task,
                    model_cfg,
                    client,
                    settings.image,
                    settings.media,
                    video_extensions,
                    timeout_override,
                    ocr_pass,
                )
                future_map[fut] = task

            with Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                bar_id = progress.add_task("Analysing memes", total=len(future_map))
                pending = set(future_map)
                interrupted_deadline: float | None = None
                while pending:
                    wait_timeout = 0.5
                    if stop_event.is_set():
                        if interrupted_deadline is None:
                            interrupted_deadline = (
                                time.monotonic() + settings.processing.shutdown_grace_s
                            )
                        remaining = interrupted_deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        wait_timeout = min(wait_timeout, remaining)
                    done, pending = wait(pending, timeout=wait_timeout, return_when=FIRST_COMPLETED)
                    for fut in done:
                        result = fut.result()
                        if result.ok:
                            finalize_success(result)
                        else:
                            finalize_failure(result)
                        progress.advance(bar_id)
                for fut in pending:
                    fut.cancel()
    finally:
        if on_main_thread and previous_handler is not None:
            signal.signal(signal.SIGINT, previous_handler)
        client.close()
        index.flush()

    if category_count_deltas and categories_file is not None:
        by_id = {c.id: c for c in categories_file.categories}
        for cid, delta in category_count_deltas.items():
            if cid in by_id:
                by_id[cid].count += delta
        categories_file.write(output_dir / "categories.json")

    summary.consume = consume_effective
    if consume_effective:
        summary.removed_from_input = _consume_sources(consume_candidates, index, output_dir)
        _remove_empty_input_subfolders(input_dir)

    summary.failed = len(index.failures)
    summary.failures = index.failures
    summary.finished_at = utcnow_iso()
    summary.duration_s = round(time.monotonic() - start_clock, 3)
    if index.failures:
        index.write_failures()
    index.write_run_summary(summary)
    return summary
