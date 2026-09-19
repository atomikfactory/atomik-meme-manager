import json
from pathlib import Path

import pytest

from atomik_meme.images import sha256_file
from atomik_meme.index import Index
from atomik_meme.processor import process_collection
from atomik_meme.schema import CategoriesFile, CategoryAssignment, Sidecar, utcnow_iso
from tests.conftest import default_analysis, has_ffmpeg, make_animated_gif, make_jpeg, make_mp4


def test_process_basic_counts_and_failure_isolation(
    sample_input_dir, tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    patch_ollama(fake_ollama)
    output_dir = tmp_path / "out"

    summary = process_collection(
        sample_input_dir, output_dir, settings, workers=1, console=quiet_console
    )

    assert summary.processed == 5  # alpha, bravo, charlie, delta, echo_tall
    assert summary.skipped_duplicate == 1  # golf_dup duplicates alpha
    assert summary.skipped_done == 0
    assert summary.failed == 1  # foxtrot_broken
    assert summary.failures[0].stage == "decode"
    assert "foxtrot_broken" in summary.failures[0].source_path

    jpgs = sorted(output_dir.glob("*.jpg"))
    gifs = sorted(output_dir.glob("*.gif"))
    # delta.gif is animated -> stored verbatim as .gif (media_type "gif"); the other four
    # (alpha/bravo/charlie/echo_tall) are static -> re-encoded/copied as .jpg.
    assert len(jpgs) == 4
    assert len(gifs) == 1
    # categories.json (the pinned NSFW category, auto-registered by default) plus one sidecar
    # per successfully processed meme.
    jsons = sorted(p for p in output_dir.glob("*.json") if p.name != "categories.json")
    assert len(jsons) == 5
    assert (output_dir / "categories.json").is_file()

    index_path = output_dir / ".meme-manager" / "index.json"
    assert index_path.is_file()
    run_dir = output_dir / ".meme-manager" / "runs"
    assert any(run_dir.glob("*-process.json"))
    assert (output_dir / ".meme-manager" / "failures.json").is_file()


def test_resume_second_run_processes_nothing_new(
    sample_input_dir, tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    patch_ollama(fake_ollama)
    output_dir = tmp_path / "out"

    first = process_collection(
        sample_input_dir, output_dir, settings, workers=1, console=quiet_console
    )
    assert first.processed == 5

    second = process_collection(
        sample_input_dir, output_dir, settings, workers=1, console=quiet_console
    )
    assert second.processed == 0
    assert second.skipped_done == 6  # 5 done + golf_dup now also matches an indexed hash
    assert second.failed == 1  # foxtrot_broken still fails every time


def test_force_reprocesses_everything(
    sample_input_dir, tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    patch_ollama(fake_ollama)
    output_dir = tmp_path / "out"

    process_collection(sample_input_dir, output_dir, settings, workers=1, console=quiet_console)
    second = process_collection(
        sample_input_dir, output_dir, settings, workers=1, force=True, console=quiet_console
    )
    assert second.processed == 5
    assert second.skipped_done == 0
    assert second.skipped_duplicate == 1
    assert second.failed == 1


def test_filename_collision_suffix_is_deterministic(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_jpeg(input_dir / "two_a.jpg", color=(10, 20, 30))
    make_jpeg(input_dir / "two_b.jpg", color=(200, 210, 220))

    fake_ollama.analysis_queue = [
        default_analysis("Same Title"),
        default_analysis("Same Title"),
    ]

    output_dir = tmp_path / "out"
    process_collection(input_dir, output_dir, settings, workers=1, console=quiet_console)

    stems = {p.stem for p in output_dir.glob("*.jpg")}
    assert "same-title" in stems
    suffixed = [s for s in stems if s.startswith("same-title-") and s != "same-title"]
    assert len(suffixed) == 1
    assert len(suffixed[0]) == len("same-title-") + 6

    # rerun with --force must produce the exact same stems (deterministic collision resolution)
    fake_ollama.analysis_queue = [
        default_analysis("Same Title"),
        default_analysis("Same Title"),
    ]
    process_collection(
        input_dir, output_dir, settings, workers=1, force=True, console=quiet_console
    )
    stems_after = {p.stem for p in output_dir.glob("*.jpg")}
    assert stems_after == stems


def test_reserved_windows_name_gets_meme_suffix(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_jpeg(input_dir / "reserved.jpg")
    fake_ollama.analysis_queue = [default_analysis("CON")]

    output_dir = tmp_path / "out"
    process_collection(input_dir, output_dir, settings, workers=1, console=quiet_console)

    stems = {p.stem for p in output_dir.glob("*.jpg")}
    assert stems == {"con-meme"}


def test_tiling_count_recorded_in_sidecar(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    from tests.conftest import make_tall_comic

    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_tall_comic(input_dir / "tall.jpg", size=(400, 4000))

    output_dir = tmp_path / "out"
    process_collection(input_dir, output_dir, settings, workers=1, console=quiet_console)

    json_files = [p for p in output_dir.glob("*.json") if p.name != "categories.json"]
    assert len(json_files) == 1
    data = json.loads(json_files[0].read_text(encoding="utf-8"))
    assert data["processing"]["image_sent"]["tiles"] == 2


def test_dry_run_never_constructs_ollama_client(
    sample_input_dir, tmp_path, settings, monkeypatch, quiet_console
):
    def _boom(**kwargs):  # pragma: no cover - should never run
        raise AssertionError("OllamaClient must not be constructed during --dry-run")

    monkeypatch.setattr("atomik_meme.processor.OllamaClient", _boom)
    output_dir = tmp_path / "out"

    summary = process_collection(
        sample_input_dir, output_dir, settings, dry_run=True, console=quiet_console
    )
    assert summary.processed == 0
    assert not output_dir.exists() or not any(output_dir.glob("*.jpg"))


def test_limit_option_caps_files_processed(
    sample_input_dir, tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    patch_ollama(fake_ollama)
    output_dir = tmp_path / "out"
    summary = process_collection(
        sample_input_dir, output_dir, settings, workers=1, limit=2, console=quiet_console
    )
    # alpha + bravo are the first two files alphabetically, both decode fine
    assert summary.processed == 2


def test_sidecar_source_hash_matches_file(
    sample_input_dir, tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    patch_ollama(fake_ollama)
    output_dir = tmp_path / "out"
    process_collection(
        output_dir=output_dir,
        input_dir=sample_input_dir,
        settings=settings,
        workers=1,
        console=quiet_console,
    )

    json_files = [p for p in output_dir.glob("*.json") if p.name != "categories.json"]
    assert json_files
    data = json.loads(json_files[0].read_text(encoding="utf-8"))
    original_path = Path(data["source"]["original_path"])
    assert data["source"]["sha256"] == sha256_file(original_path)
    assert "/" in data["source"]["original_path"]  # forward slashes, even on Windows


def test_zero_workers_raises_value_error(sample_input_dir, tmp_path, settings, quiet_console):
    with pytest.raises(ValueError):
        process_collection(
            sample_input_dir, tmp_path / "out", settings, workers=0, console=quiet_console
        )


def test_resume_reconciles_sidecars_missing_from_index_json(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    """A crash between periodic index flushes must not cause reprocessing or duplicates.

    Simulated here by writing a fully-formed sidecar+jpg pair straight to disk, without ever
    touching index.json - exactly what's left behind if the process is killed after a sidecar
    is written but before the next index flush.
    """
    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_jpeg(input_dir / "one.jpg", color=(11, 22, 33))
    output_dir = tmp_path / "out"

    # First, a normal run to get a realistic sidecar (with a real sha256) written to disk.
    fake_ollama.analysis_queue = [default_analysis("Already Done")]
    process_collection(input_dir, output_dir, settings, workers=1, console=quiet_console)

    # Now simulate the crash: delete index.json entirely (as if it never got flushed).
    index_path = output_dir / ".meme-manager" / "index.json"
    assert index_path.is_file()
    index_path.unlink()

    # A second run must still recognise the file as already done via reconciliation - not
    # via a rebuild-from-scratch coincidence, but a real gap-filling scan - and must not
    # create a second, duplicate pair for it.
    second = process_collection(input_dir, output_dir, settings, workers=1, console=quiet_console)
    assert second.processed == 0
    assert second.skipped_done == 1
    assert len(list(output_dir.glob("*.jpg"))) == 1
    assert len([p for p in output_dir.glob("*.json") if p.name != "categories.json"]) == 1


def test_force_reprocess_keeps_single_pair_in_existing_category_folder(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    """--force re-analysing a meme that produces a NEW title must not orphan the old pair.

    The new pair must land in the SAME (already-categorised) folder as the old one, carry
    over the existing `category`, and the old files must be gone - exactly one pair for
    this sha256 afterwards, and the index must point at it.
    """
    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_jpeg(input_dir / "one.jpg", color=(44, 55, 66))
    output_dir = tmp_path / "out"

    fake_ollama.analysis_queue = [default_analysis("Original Title")]
    process_collection(input_dir, output_dir, settings, workers=1, console=quiet_console)

    # Simulate this meme having already been categorised: move its pair into a category
    # folder, give its sidecar a `category`, and update the index to match.
    old_jpg = output_dir / "original-title.jpg"
    old_json = output_dir / "original-title.json"
    cat_dir = output_dir / "03-work"
    cat_dir.mkdir()
    sidecar = Sidecar.read(old_json)
    sidecar.category = CategoryAssignment(
        id=3,
        name="work",
        confidence=0.9,
        assigned_at=utcnow_iso(),
        model="qwen3.5:4b",
        run_id="abc123def456",
    )
    old_jpg.rename(cat_dir / "original-title.jpg")
    old_json.unlink()
    sidecar.write(cat_dir / "original-title.json")

    index = Index.load_or_rebuild(output_dir)
    sha = sidecar.source.sha256
    entry = index.get(sha)
    entry.stem = "original-title"
    entry.relpath = "03-work/original-title.jpg"
    entry.category_id = 3
    index.flush()

    # Re-analysing now returns a DIFFERENT title.
    fake_ollama.analysis_queue = [default_analysis("Brand New Title")]
    summary = process_collection(
        input_dir, output_dir, settings, workers=1, force=True, console=quiet_console
    )
    assert summary.processed == 1

    # No orphan left anywhere, and the new pair lives in the original category folder.
    assert not (output_dir / "brand-new-title.jpg").exists()
    assert not (output_dir / "original-title.jpg").exists()
    assert not (cat_dir / "original-title.jpg").exists()
    assert not (cat_dir / "original-title.json").exists()
    assert (cat_dir / "brand-new-title.jpg").is_file()
    assert (cat_dir / "brand-new-title.json").is_file()

    new_sidecar = Sidecar.read(cat_dir / "brand-new-title.json")
    assert new_sidecar.category is not None
    assert new_sidecar.category.id == 3
    assert new_sidecar.category.name == "work"

    reloaded = Index.load_or_rebuild(output_dir)
    new_entry = reloaded.get(sha)
    assert new_entry.stem == "brand-new-title"
    assert new_entry.relpath == "03-work/brand-new-title.jpg"
    assert new_entry.category_id == 3


def test_first_n_workers_tasks_get_doubled_timeout(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for i in range(4):
        make_jpeg(input_dir / f"m{i}.jpg", color=(i * 10, i * 20, i * 30))
    patch_ollama(fake_ollama)

    output_dir = tmp_path / "out"
    process_collection(
        output_dir=output_dir,
        input_dir=input_dir,
        settings=settings,
        workers=2,
        console=quiet_console,
    )

    analysis_calls = [c for c in fake_ollama.calls_log if c["title"] == "Analysis"]
    assert len(analysis_calls) == 4
    doubled = [c for c in analysis_calls if c["timeout_override"] == settings.ollama.timeout_s * 2]
    plain = [c for c in analysis_calls if c["timeout_override"] is None]
    assert len(doubled) == 2  # workers=2 -> the first two dispatched tasks
    assert len(plain) == 2


# --- Media placement: gif/video into <image|gif|video>/ subfolders when routed by hint/nsfw ---


def test_index_reconciliation_uses_correct_extension_for_gif(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    """Regression: `Index.reconcile_with_disk` used to assume every sidecar's media sibling was
    `.jpg`, which silently pointed a rebuilt index at a nonexistent file for gif/video pairs."""
    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_animated_gif(input_dir / "anim.gif", frames=4)
    output_dir = tmp_path / "out"
    process_collection(input_dir, output_dir, settings, workers=1, console=quiet_console)

    # Simulate a crash before index.json was ever flushed for this item.
    (output_dir / ".meme-manager" / "index.json").unlink()

    reloaded = Index.load_or_rebuild(output_dir)
    (entry,) = reloaded.entries.values()
    assert entry.relpath.endswith(".gif")
    assert (output_dir / entry.relpath).is_file()


def test_gif_and_video_stored_verbatim_when_left_flat(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_animated_gif(input_dir / "anim.gif", frames=6)
    output_dir = tmp_path / "out"

    summary = process_collection(input_dir, output_dir, settings, workers=1, console=quiet_console)
    assert summary.processed == 1
    gifs = list(output_dir.glob("*.gif"))
    assert len(gifs) == 1
    data = json.loads(gifs[0].with_suffix(".json").read_text(encoding="utf-8"))
    assert data["file"]["media_type"] == "gif"


def test_gif_sidecar_gets_duration_fps_frame_count_filled_in(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    """Regression: gif sidecars used to have duration_s/fps/frame_count all null (only video
    ones were filled in) - filled from Pillow's per-frame timing, not from the vision model."""
    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_animated_gif(input_dir / "anim.gif", frames=8)  # 80ms/frame in conftest -> 0.64s total
    output_dir = tmp_path / "out"

    process_collection(input_dir, output_dir, settings, workers=1, console=quiet_console)

    gif_path = next(output_dir.glob("*.gif"))
    file_info = json.loads(gif_path.with_suffix(".json").read_text(encoding="utf-8"))["file"]
    assert file_info["media_type"] == "gif"
    assert file_info["frame_count"] == 8
    assert file_info["duration_s"] == pytest.approx(0.64, rel=0.05)
    assert file_info["fps"] == pytest.approx(8 / 0.64, rel=0.05)
    assert file_info["has_audio"] is None


# --- Group hints ----------------------------------------------------------------


def test_unmatched_category_flag_creates_pinned_id_11_and_writes_categories_json(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_jpeg(input_dir / "one.jpg")
    output_dir = tmp_path / "out"

    summary = process_collection(
        input_dir,
        output_dir,
        settings,
        workers=1,
        console=quiet_console,
        category_hint="Reaction",
    )
    assert summary.hinted == 1
    # the pinned NSFW category (id 0) is auto-registered at the start of every run (nsfw
    # enabled by default), *and* the hint creates the new "reaction" user category (id 11).
    created_by_id = {c.id: c for c in summary.categories_created}
    assert set(created_by_id) == {0, 11}
    assert created_by_id[11].folder == "11-reaction"
    assert created_by_id[11].name == "reaction"

    placed = list((output_dir / "11-reaction" / "image").glob("*.jpg"))
    assert len(placed) == 1
    sidecar = Sidecar.read(placed[0].with_suffix(".json"))
    assert sidecar.category is not None
    assert sidecar.category.id == 11
    assert sidecar.category.source == "hint"

    cf = CategoriesFile.read(output_dir / "categories.json")
    assert len(cf.categories) == 2  # pinned nsfw (0) + the new "reaction" user category (11)
    cat = next(c for c in cf.categories if c.id == 11)
    assert cat.pinned is True and cat.source == "user"
    assert cat.count == 1


def test_subfolder_hint_places_file_without_cli_flag(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    (input_dir / "Office Software Humor").mkdir(parents=True)
    make_jpeg(input_dir / "Office Software Humor" / "excel.jpg")
    output_dir = tmp_path / "out"

    summary = process_collection(input_dir, output_dir, settings, workers=1, console=quiet_console)
    assert summary.hinted == 1
    created_folders = {c.folder for c in summary.categories_created}
    assert "11-office-software-humor" in created_folders  # plus the auto-registered nsfw (0)
    placed = list((output_dir / "11-office-software-humor" / "image").glob("*.jpg"))
    assert len(placed) == 1


def test_cli_category_flag_takes_precedence_over_subfolder_hint(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    (input_dir / "some-subfolder").mkdir(parents=True)
    make_jpeg(input_dir / "some-subfolder" / "one.jpg")
    output_dir = tmp_path / "out"

    process_collection(
        input_dir,
        output_dir,
        settings,
        workers=1,
        console=quiet_console,
        category_hint="Explicit Flag Category",
    )
    assert list((output_dir / "11-explicit-flag-category" / "image").glob("*.jpg"))
    assert not (output_dir / "12-some-subfolder").exists()


def test_hint_slug_matches_existing_category_folder_verbatim(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    """`"00 - NSFW"` (verbatim folder name with spaces) must resolve via slug matching."""
    patch_ollama(fake_ollama)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    CategoriesFile(
        generated_at=utcnow_iso(),
        model="",
        profile="",
        run_id="abc123",
        collection_size=0,
        layout_version=2,
        categories=[
            {
                "id": 0,
                "name": "nsfw",
                "folder": "00 - NSFW",
                "description": "",
                "keywords": [],
                "count": 0,
                "pinned": True,
                "rule": "nsfw",
                "source": "rule",
            }
        ],
    ).write(output_dir / "categories.json")

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_jpeg(input_dir / "one.jpg")

    summary = process_collection(
        input_dir,
        output_dir,
        settings,
        workers=1,
        console=quiet_console,
        category_hint="00 - NSFW",
    )
    assert summary.hinted == 1
    assert summary.categories_created == []  # nsfw already existed; matched, not created
    placed = list((output_dir / "00 - NSFW" / "image").glob("*.jpg"))
    assert len(placed) == 1


def test_hint_nsfw_resolves_to_pinned_id_0_when_categories_json_lacks_it(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    """Regression: a categories.json that only has ids 1-10 (e.g. produced by `migrate` on a
    v1 collection before the NSFW-adoption fix, or any collection that never routed an nsfw
    meme yet) must not make `--category nsfw` create a brand-new pinned user category -
    `ensure_nsfw_category` must run before hint resolution and register id 0 first."""
    patch_ollama(fake_ollama)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    CategoriesFile(
        generated_at=utcnow_iso(),
        model="",
        profile="",
        run_id="abc123",
        collection_size=0,
        layout_version=2,
        categories=[
            {
                "id": i,
                "name": f"cat{i}",
                "folder": f"{i:02d}-cat{i}",
                "description": "",
                "keywords": [],
                "count": 0,
            }
            for i in range(1, 11)
        ],
    ).write(output_dir / "categories.json")

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_jpeg(input_dir / "one.jpg")

    summary = process_collection(
        input_dir, output_dir, settings, workers=1, console=quiet_console, category_hint="nsfw"
    )
    assert summary.hinted == 1
    placed = list((output_dir / "00 - NSFW" / "image").glob("*.jpg"))
    assert len(placed) == 1

    cf = CategoriesFile.read(output_dir / "categories.json")
    assert len(cf.categories) == 11  # the 10 pre-existing ones + the newly-registered id 0
    assert not any(c.id >= 11 for c in cf.categories)  # no new pinned USER category created
    nsfw_cat = next(c for c in cf.categories if c.id == 0)
    assert nsfw_cat.folder == "00 - NSFW"
    assert nsfw_cat.pinned is True and nsfw_cat.rule == "nsfw"

    # "00 - NSFW" as the hint also resolves to the same id 0, not a second category.
    second_input = tmp_path / "input2"
    second_input.mkdir()
    make_jpeg(second_input / "two.jpg", color=(5, 6, 7))
    process_collection(
        second_input,
        output_dir,
        settings,
        workers=1,
        console=quiet_console,
        category_hint="00 - NSFW",
    )
    cf2 = CategoriesFile.read(output_dir / "categories.json")
    assert not any(c.id >= 11 for c in cf2.categories)
    assert len(list((output_dir / "00 - NSFW" / "image").glob("*.jpg"))) == 2


def test_hint_category_name_is_stored_as_slug_and_variants_resolve_to_same_id(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    """Matching is by slug, so the category's own `name` should already be a slug (not the
    raw, spaced text the user typed) - the original text is preserved in `description` instead.
    Later hints that slugify to the same thing must resolve to the SAME category, never create
    another one."""
    from atomik_meme.naming import slugify

    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_jpeg(input_dir / "one.jpg")
    output_dir = tmp_path / "out"

    summary = process_collection(
        input_dir,
        output_dir,
        settings,
        workers=1,
        console=quiet_console,
        category_hint="Test Pinned",
    )
    expected_slug = slugify("Test Pinned", max_len=30, drop_leading_articles=False)
    assert expected_slug == "test-pinned"

    created = next(c for c in summary.categories_created if c.folder == "11-test-pinned")
    assert created.name == expected_slug

    cf = CategoriesFile.read(output_dir / "categories.json")
    cat = next(c for c in cf.categories if c.id == 11)
    assert cat.name == expected_slug
    assert '"Test Pinned"' in cat.description  # original text preserved in the description

    sidecar_json = next((output_dir / "11-test-pinned" / "image").glob("*.json"))
    sidecar = Sidecar.read(sidecar_json)
    assert sidecar.category.name == expected_slug  # sidecars store the slug too

    for i, variant in enumerate(("test pinned", "TEST-PINNED", "11-test-pinned")):
        # Windows filesystems are case-insensitive, so the directory name can't depend on the
        # variant's casing alone (only what's *sent as the hint* should distinguish them).
        sub_input = tmp_path / f"in-variant-{i}"
        sub_input.mkdir()
        make_jpeg(sub_input / "x.jpg", color=((i + 1) * 30, 1, 2))
        process_collection(
            sub_input,
            output_dir,
            settings,
            workers=1,
            console=quiet_console,
            category_hint=variant,
        )

    cf_after = CategoriesFile.read(output_dir / "categories.json")
    assert not any(c.id == 12 for c in cf_after.categories)  # no duplicate category created
    final_cat = next(c for c in cf_after.categories if c.id == 11)
    assert final_cat.count == 4  # original file + the 3 resolved variants


# --- NSFW auto-routing at process time -------------------------------------------


def test_nsfw_meme_auto_routed_to_pinned_folder(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_jpeg(input_dir / "one.jpg")
    fake_ollama.analysis_queue = [{**default_analysis("risque"), "nsfw": True}]
    output_dir = tmp_path / "out"

    process_collection(input_dir, output_dir, settings, workers=1, console=quiet_console)

    placed = list((output_dir / "00 - NSFW" / "image").glob("*.jpg"))
    assert len(placed) == 1
    sidecar = Sidecar.read(placed[0].with_suffix(".json"))
    assert sidecar.category.id == 0
    assert sidecar.category.source == "rule"

    cf = CategoriesFile.read(output_dir / "categories.json")
    nsfw_cat = next(c for c in cf.categories if c.id == 0)
    assert nsfw_cat.folder == "00 - NSFW"
    assert nsfw_cat.pinned is True
    assert nsfw_cat.rule == "nsfw"
    assert nsfw_cat.count == 1


def test_nsfw_rule_disabled_leaves_meme_uncategorised(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    settings.categorize.nsfw.enabled = False
    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_jpeg(input_dir / "one.jpg")
    fake_ollama.analysis_queue = [{**default_analysis("risque"), "nsfw": True}]
    output_dir = tmp_path / "out"

    process_collection(input_dir, output_dir, settings, workers=1, console=quiet_console)

    assert not (output_dir / "00 - NSFW").exists()
    assert len(list(output_dir.glob("*.jpg"))) == 1


def test_hint_wins_over_nsfw_rule(tmp_path, settings, fake_ollama, patch_ollama, quiet_console):
    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_jpeg(input_dir / "one.jpg")
    fake_ollama.analysis_queue = [{**default_analysis("risque"), "nsfw": True}]
    output_dir = tmp_path / "out"

    process_collection(
        input_dir,
        output_dir,
        settings,
        workers=1,
        console=quiet_console,
        category_hint="work stuff",
    )

    assert not (output_dir / "00 - NSFW").exists()
    placed = list((output_dir / "11-work-stuff" / "image").glob("*.jpg"))
    assert len(placed) == 1
    sidecar = Sidecar.read(placed[0].with_suffix(".json"))
    assert sidecar.category.source == "hint"


# --- ffmpeg missing -> clear per-file failure -----------------------------------------


@pytest.mark.skipif(not has_ffmpeg(), reason="need a real ffmpeg to author the test mp4")
def test_video_fails_clearly_when_ffmpeg_is_missing(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    settings.media.ffmpeg_path = "definitely-not-a-real-ffmpeg-binary-xyz"
    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_mp4(input_dir / "clip.mp4", duration_s=1.0)
    output_dir = tmp_path / "out"

    summary = process_collection(input_dir, output_dir, settings, workers=1, console=quiet_console)
    assert summary.failed == 1
    assert summary.failures[0].stage == "decode"
    assert "ffmpeg" in summary.failures[0].message.lower()


# --- --consume ---------------------------------------------------------------------


def test_consume_deletes_source_and_removes_emptied_subfolder(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    (input_dir / "sub").mkdir(parents=True)
    make_jpeg(input_dir / "root.jpg", color=(10, 20, 30))
    make_jpeg(input_dir / "sub" / "nested.jpg", color=(200, 150, 50))
    output_dir = tmp_path / "out"

    summary = process_collection(
        input_dir, output_dir, settings, workers=1, console=quiet_console, consume=True
    )
    assert summary.processed == 2
    assert summary.consume is True
    assert summary.removed_from_input == 2
    assert not (input_dir / "root.jpg").exists()
    assert not (input_dir / "sub" / "nested.jpg").exists()
    assert not (input_dir / "sub").exists()  # emptied subfolder removed
    assert input_dir.exists()  # never the input root itself


def test_consume_never_removes_a_failed_source(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_jpeg(input_dir / "good.jpg")
    (input_dir / "bad.jpg").write_bytes(b"not-an-image")
    output_dir = tmp_path / "out"

    summary = process_collection(
        input_dir, output_dir, settings, workers=1, console=quiet_console, consume=True
    )
    assert summary.failed == 1
    assert not (input_dir / "good.jpg").exists()
    assert (input_dir / "bad.jpg").exists()  # failed source is never removed


def test_consume_has_no_effect_on_dry_run(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_jpeg(input_dir / "one.jpg")
    output_dir = tmp_path / "out"

    process_collection(
        input_dir,
        output_dir,
        settings,
        workers=1,
        console=quiet_console,
        consume=True,
        dry_run=True,
    )
    assert (input_dir / "one.jpg").exists()


def test_dry_run_with_consume_previews_removal_count(
    sample_input_dir, tmp_path, settings, quiet_console
):
    output_dir = tmp_path / "out"
    summary = process_collection(
        sample_input_dir,
        output_dir,
        settings,
        dry_run=True,
        consume=True,
        console=quiet_console,
    )
    assert summary.consume is True
    # every file that would be processed/skipped (i.e. not obviously excluded) is previewed as
    # a removal candidate - a dry run never decodes files, so this can't know about decode
    # failures ahead of time (foxtrot_broken.jpg is still counted here, unlike a real run).
    assert summary.removed_from_input == len(list(sample_input_dir.glob("*")))
    assert not any(output_dir.glob("*.jpg")) if output_dir.exists() else True


def test_consume_removes_already_done_and_duplicate_sources_on_rerun(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    make_jpeg(input_dir / "one.jpg", color=(1, 2, 3))
    output_dir = tmp_path / "out"

    # First run without --consume: source stays; sha256 gets indexed.
    process_collection(input_dir, output_dir, settings, workers=1, console=quiet_console)
    assert (input_dir / "one.jpg").exists()

    # Add a byte-identical duplicate, then rerun WITH --consume: "one.jpg" is now
    # "already done" and "dup.jpg" is a same-run duplicate - both should be removed
    # because the indexed output pair is verified on disk.
    import shutil as _shutil

    _shutil.copyfile(input_dir / "one.jpg", input_dir / "dup.jpg")
    summary = process_collection(
        input_dir, output_dir, settings, workers=1, console=quiet_console, consume=True
    )
    # Both now hash to a sha256 already present in the index from the first run (scanned in
    # sorted order: "dup.jpg" before "one.jpg") - both count as "already done", not a
    # same-run "duplicate"; the indexed pair is verified on disk for both before removal.
    assert summary.skipped_done == 2
    assert summary.skipped_duplicate == 0
    assert summary.removed_from_input == 2
    assert not (input_dir / "one.jpg").exists()
    assert not (input_dir / "dup.jpg").exists()


def test_consume_is_on_by_default(tmp_path, fake_ollama, quiet_console, patch_ollama):
    """Built-in defaults (remove_from_input=True) and no --consume flag: sources are removed."""
    from atomik_meme.config import Settings

    patch_ollama(fake_ollama)
    settings = Settings()
    assert settings.processing.remove_from_input is True
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    make_jpeg(input_dir / "one.jpg")
    output_dir = tmp_path / "out"
    fake_ollama.analysis_queue = [default_analysis("One")]
    summary = process_collection(
        input_dir, output_dir, settings, workers=1, console=quiet_console, consume=None
    )
    assert summary.processed == 1
    assert summary.removed_from_input == 1
    assert not (input_dir / "one.jpg").exists()
    assert len(list(output_dir.glob("*.jpg"))) == 1
