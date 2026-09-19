import json
import re

from atomik_meme.categories import migrate_collection
from atomik_meme.categorizer import categorize_collection
from atomik_meme.processor import process_collection
from atomik_meme.schema import CategoriesFile, Sidecar
from tests.conftest import default_analysis, default_discovery, make_jpeg

NN_RE = re.compile(r"^\d{2}-")


def _build_initial_collection(tmp_path, settings, fake, patch_ollama, quiet_console, n=12):
    """Process `n` synthetic memes, each titled 'cat item <i>', into a fresh output dir."""
    patch_ollama(fake)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for i in range(n):
        make_jpeg(
            input_dir / f"m{i:02d}.jpg", color=((i * 37) % 256, (i * 61) % 256, (i * 89) % 256)
        )
    fake.analysis_queue = [default_analysis(f"cat item {i}") for i in range(n)]

    output_dir = tmp_path / "out"
    process_collection(input_dir, output_dir, settings, workers=1, console=quiet_console)
    return output_dir


def _index_by_title(categories, batch):
    """Helper for building assignment_handlers keyed by the numeric suffix in the title."""
    return {int(rec["title"].split()[-1]): rec for rec in batch}


def _folder_dirs(output_dir):
    return sorted(d.name for d in output_dir.iterdir() if d.is_dir() and NN_RE.match(d.name))


def test_full_discovery_exactly_ten_categories_others_last_and_low_confidence(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    output_dir = _build_initial_collection(
        tmp_path, settings, fake_ollama, patch_ollama, quiet_console, n=12
    )

    def handler(categories, batch):
        cat_ids = [c["id"] for c in categories]
        assignments = []
        for rec in batch:
            idx = int(rec["title"].split()[-1])
            if idx < 5:
                cat_id, conf = cat_ids[0], 0.9
            elif idx < 7:
                cat_id, conf = cat_ids[1], 0.9
            elif idx == 7:
                cat_id, conf = cat_ids[2], 0.9
            elif idx == 8:
                cat_id, conf = cat_ids[3], 0.1  # below default min_confidence (0.5) -> others
            else:
                cat_id, conf = cat_ids[4], 0.9
            assignments.append(
                {"id": rec["id"], "category_id": cat_id, "confidence": conf, "reason": "t"}
            )
        return assignments

    fake_ollama.assignment_handler = handler

    summary = categorize_collection(output_dir, settings, console=quiet_console)
    assert summary.categorized == 12

    categories_file = CategoriesFile.read(output_dir / "categories.json")
    # 9 discovered + others (10) + the pinned NSFW category (0), auto-registered by default
    # even though nothing actually routed there this run (count 0).
    assert len(categories_file.categories) == 11
    others = categories_file.categories[-1]
    assert others.id == 10
    assert others.name == settings.categorize.others_name

    by_id = {c.id: c for c in categories_file.categories}
    assert by_id[0].pinned is True and by_id[0].count == 0
    assert by_id[1].count == 5  # the idx<5 group is the largest -> reordered to id 1
    assert by_id[1].folder.startswith("01-")

    # low-confidence item (idx 8) must have landed in "others" with its guess kept as `suggested`
    idx8_sidecar_path = next(
        p
        for p in output_dir.rglob("*.json")
        if p.name != "categories.json" and "cat-item-8" in p.name
    )
    sidecar = Sidecar.read(idx8_sidecar_path)
    assert sidecar.category is not None
    assert sidecar.category.id == 10
    assert sidecar.category.suggested is not None

    # files must be moved in pairs, into the v2 <category>/<image|gif|video>/ layout: every
    # sidecar json under a category folder's media subfolder has its jpg sibling
    for json_path in output_dir.rglob("*.json"):
        if json_path.name == "categories.json" or ".meme-manager" in json_path.parts:
            continue
        if json_path.parent.name in ("image", "gif", "video") and NN_RE.match(
            json_path.parent.parent.name
        ):
            assert json_path.with_suffix(".jpg").is_file()
    # and nothing should remain uncategorised at the root (categories.json itself excepted)
    assert not any(output_dir.glob("*.jpg"))
    assert not any(p for p in output_dir.glob("*.json") if p.name != "categories.json")


def test_discovery_retries_on_invalid_output(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    output_dir = _build_initial_collection(
        tmp_path, settings, fake_ollama, patch_ollama, quiet_console, n=12
    )

    invalid = {"categories": default_discovery(5)["categories"]}  # wrong count (5, not 9)
    fake_ollama.discovery_queue = [invalid]  # then falls back to the valid default (9) on retry

    summary = categorize_collection(output_dir, settings, console=quiet_console)
    categories_file = CategoriesFile.read(output_dir / "categories.json")
    assert len(categories_file.categories) == 11  # + the pinned NSFW category (0)
    assert summary.categorized == 12


def test_rediscover_moves_files_and_removes_empty_folders(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    output_dir = _build_initial_collection(
        tmp_path, settings, fake_ollama, patch_ollama, quiet_console, n=12
    )

    def spread_handler(categories, batch):
        cat_ids = [c["id"] for c in categories]
        assignments = []
        for rec in batch:
            idx = int(rec["title"].split()[-1])
            cat_id = cat_ids[0] if idx < 8 else cat_ids[1]
            assignments.append(
                {"id": rec["id"], "category_id": cat_id, "confidence": 0.9, "reason": "t"}
            )
        return assignments

    fake_ollama.assignment_handler = spread_handler
    categorize_collection(output_dir, settings, console=quiet_console)
    folders_before = _folder_dirs(output_dir)
    assert len(folders_before) >= 2

    # rediscover: reassign EVERYTHING into one category -> old folders empty out and vanish
    def single_handler(categories, batch):
        cat_id = categories[0]["id"]
        return [
            {"id": rec["id"], "category_id": cat_id, "confidence": 0.9, "reason": "t"}
            for rec in batch
        ]

    fake_ollama.assignment_handler = single_handler
    categorize_collection(output_dir, settings, rediscover=True, console=quiet_console)

    folders_after = _folder_dirs(output_dir)
    assert len(folders_after) == 1
    remaining_jpgs = list((output_dir / folders_after[0] / "image").glob("*.jpg"))
    assert len(remaining_jpgs) == 12
    for old_folder in folders_before:
        if old_folder != folders_after[0]:
            assert not (output_dir / old_folder).exists()


def test_assign_only_mode_keeps_numbering_stable(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    output_dir = _build_initial_collection(
        tmp_path, settings, fake_ollama, patch_ollama, quiet_console, n=12
    )

    def handler(categories, batch):
        cat_ids = [c["id"] for c in categories]
        assignments = []
        for rec in batch:
            idx = int(rec["title"].split()[-1])
            cat_id = cat_ids[0] if idx < 5 else (cat_ids[1] if idx < 7 else cat_ids[2])
            assignments.append(
                {"id": rec["id"], "category_id": cat_id, "confidence": 0.9, "reason": "t"}
            )
        return assignments

    fake_ollama.assignment_handler = handler
    categorize_collection(output_dir, settings, console=quiet_console)

    before = {
        c.id: (c.name, c.folder)
        for c in CategoriesFile.read(output_dir / "categories.json").categories
    }
    top_category_id = max(
        CategoriesFile.read(output_dir / "categories.json").categories,
        key=lambda c: c.count if c.id != 10 else -1,
    ).id
    top_folder = before[top_category_id][1]

    # simulate a fresh `process` run adding 3 brand-new, not-yet-categorised memes
    new_input = tmp_path / "input2"
    new_input.mkdir()
    for i in range(3):
        make_jpeg(new_input / f"new{i}.jpg", color=(5 + i, 6 + i, 7 + i))
    fake_ollama.analysis_queue = [default_analysis(f"newcomer {i}") for i in range(3)]
    process_collection(new_input, output_dir, settings, workers=1, console=quiet_console)

    new_titles_root = [p for p in output_dir.glob("newcomer*.json")]
    assert len(new_titles_root) == 3  # freshly processed, still at root, category == null

    def assign_only_handler(categories, batch):
        # send every newcomer to the same (already-existing, already-numbered) top category
        return [
            {"id": rec["id"], "category_id": top_category_id, "confidence": 0.9, "reason": "t"}
            for rec in batch
        ]

    fake_ollama.assignment_handler = assign_only_handler
    summary = categorize_collection(output_dir, settings, rediscover=False, console=quiet_console)

    after_file = CategoriesFile.read(output_dir / "categories.json")
    after = {c.id: (c.name, c.folder) for c in after_file.categories}
    assert after == before  # ids/names/folders unchanged - only counts may differ
    assert not any(output_dir.glob("newcomer*.json"))  # all moved out of root
    moved = list((output_dir / top_folder / "image").glob("newcomer*.jpg"))
    assert len(moved) == 3
    assert summary.categorized == 15


def test_dry_run_on_fresh_collection_does_not_crash_or_write_anything(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    """Regression: categorize --dry-run on a brand-new collection used to crash with an
    AttributeError because the Ollama client was only built when `not dry_run`, even though
    discovery/assignment always need one. `process` (nsfw enabled by default) already leaves
    a categories.json behind with just the pinned NSFW category - --dry-run must not touch it
    further."""
    output_dir = _build_initial_collection(
        tmp_path, settings, fake_ollama, patch_ollama, quiet_console, n=12
    )
    categories_path = output_dir / "categories.json"
    before = categories_path.read_text(encoding="utf-8")

    summary = categorize_collection(output_dir, settings, dry_run=True, console=quiet_console)

    assert summary.categorized == 12
    assert categories_path.read_text(encoding="utf-8") == before  # unchanged by the dry run
    assert len(list(output_dir.glob("*.jpg"))) == 12  # nothing moved
    assert not _folder_dirs(output_dir)


def test_dry_run_with_rediscover_on_existing_collection_changes_nothing_on_disk(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    output_dir = _build_initial_collection(
        tmp_path, settings, fake_ollama, patch_ollama, quiet_console, n=12
    )
    categorize_collection(output_dir, settings, console=quiet_console)
    before_categories = CategoriesFile.read(output_dir / "categories.json")
    before_folders = _folder_dirs(output_dir)

    def single_handler(categories, batch):
        cat_id = categories[0]["id"]
        return [
            {"id": rec["id"], "category_id": cat_id, "confidence": 0.9, "reason": "t"}
            for rec in batch
        ]

    fake_ollama.assignment_handler = single_handler
    categorize_collection(
        output_dir, settings, rediscover=True, dry_run=True, console=quiet_console
    )

    after_categories = CategoriesFile.read(output_dir / "categories.json")
    assert after_categories == before_categories  # untouched by the dry run
    assert _folder_dirs(output_dir) == before_folders  # no files moved


def test_category_count_6_end_to_end(tmp_path, settings, fake_ollama, patch_ollama, quiet_console):
    settings.categorize.category_count = 6
    output_dir = _build_initial_collection(
        tmp_path, settings, fake_ollama, patch_ollama, quiet_console, n=12
    )

    summary = categorize_collection(output_dir, settings, console=quiet_console)
    assert summary.categorized == 12

    categories_file = CategoriesFile.read(output_dir / "categories.json")
    assert len(categories_file.categories) == 7  # + the pinned NSFW category (0)
    others = categories_file.categories[-1]
    assert others.id == 6
    assert others.name == settings.categorize.others_name
    assert others.folder.startswith("06-")


def test_finalize_assignment_degrades_unknown_category_id_to_others(caplog):
    """Direct unit test of the "never KeyError" defence (review finding #2/#5): even if a
    category_id somehow reaches this point outside the known set, it must be routed to
    `others` with a logged warning and the original guess preserved as `suggested` - never
    raise, and never silently accepted as a real category."""
    import logging
    from types import SimpleNamespace

    from atomik_meme.categorizer import _finalize_assignment

    item = SimpleNamespace(
        id="deadbeef00000000", category_id=999, confidence=0.95, reason="looks right"
    )
    with caplog.at_level(logging.WARNING):
        result = _finalize_assignment(
            item, min_confidence=0.5, others_id=10, valid_ids=set(range(1, 11))
        )
    assert result["category_id"] == 10
    assert result["suggested"] == 999
    assert any("unknown category_id" in r.message for r in caplog.records)


def test_missing_jpg_still_moves_sidecar_and_logs_warning(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console, caplog
):
    import logging

    output_dir = _build_initial_collection(
        tmp_path, settings, fake_ollama, patch_ollama, quiet_console, n=12
    )
    # Simulate a jpg that went missing (e.g. manual deletion) while its sidecar remains.
    orphan_json = next(output_dir.glob("*cat-item-0*.json"))
    orphan_json.with_suffix(".jpg").unlink()

    def handler(categories, batch):
        cat_id = categories[0]["id"]
        return [
            {"id": rec["id"], "category_id": cat_id, "confidence": 0.9, "reason": "t"}
            for rec in batch
        ]

    fake_ollama.assignment_handler = handler
    with caplog.at_level(logging.WARNING):
        summary = categorize_collection(output_dir, settings, console=quiet_console)

    assert any("missing" in r.message for r in caplog.records)
    # the sidecar itself must still have been moved despite its jpg being gone
    moved_json = list(output_dir.rglob("*cat-item-0*.json"))
    moved_json = [p for p in moved_json if NN_RE.match(p.parent.parent.name)]
    assert len(moved_json) == 1
    assert summary.categorized == 12


# --- Pinned categories excluded from discovery/assignment, never touched --------


def test_discovery_excludes_pinned_hinted_memes_and_leaves_them_untouched(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    output_dir = _build_initial_collection(
        tmp_path, settings, fake_ollama, patch_ollama, quiet_console, n=12
    )

    # Two more memes, hinted directly into a pinned user category at process time.
    pinned_input = tmp_path / "pinned_input"
    pinned_input.mkdir()
    make_jpeg(pinned_input / "p0.jpg", color=(9, 8, 7))
    make_jpeg(pinned_input / "p1.jpg", color=(1, 2, 3))
    fake_ollama.analysis_queue = [
        default_analysis("pinned item 0"),
        default_analysis("pinned item 1"),
    ]
    process_collection(
        pinned_input,
        output_dir,
        settings,
        workers=1,
        console=quiet_console,
        category_hint="user pin",
    )
    pinned_before = sorted((output_dir / "11-user-pin" / "image").glob("*.jpg"))
    assert len(pinned_before) == 2

    summary = categorize_collection(output_dir, settings, console=quiet_console)
    # only the 12 non-pinned memes go through discovery/assignment
    assert summary.categorized == 12

    cf = CategoriesFile.read(output_dir / "categories.json")
    pinned_cat = next(c for c in cf.categories if c.id == 11)
    assert pinned_cat.pinned is True
    assert pinned_cat.folder == "11-user-pin"
    assert pinned_cat.count == 2
    # ids 1..10 are the discovered set, untouched by the pinned category's presence
    assert {c.id for c in cf.categories if not c.pinned} == set(range(1, 11))

    pinned_after = sorted((output_dir / "11-user-pin" / "image").glob("*.jpg"))
    assert pinned_after == pinned_before  # never moved


def test_rediscover_does_not_touch_pinned_members(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    output_dir = _build_initial_collection(
        tmp_path, settings, fake_ollama, patch_ollama, quiet_console, n=12
    )
    pinned_input = tmp_path / "pinned_input"
    pinned_input.mkdir()
    make_jpeg(pinned_input / "p0.jpg", color=(9, 8, 7))
    fake_ollama.analysis_queue = [default_analysis("pinned item 0")]
    process_collection(
        pinned_input,
        output_dir,
        settings,
        workers=1,
        console=quiet_console,
        category_hint="keepers",
    )

    categorize_collection(output_dir, settings, console=quiet_console)

    def single_handler(categories, batch):
        cat_id = categories[0]["id"]
        return [
            {"id": rec["id"], "category_id": cat_id, "confidence": 0.9, "reason": "t"}
            for rec in batch
        ]

    fake_ollama.assignment_handler = single_handler
    categorize_collection(output_dir, settings, rediscover=True, console=quiet_console)

    assert (output_dir / "11-keepers" / "image" / "pinned-item-0.jpg").is_file()
    cf = CategoriesFile.read(output_dir / "categories.json")
    pinned_cat = next(c for c in cf.categories if c.id == 11)
    assert pinned_cat.folder == "11-keepers"
    assert pinned_cat.count == 1


# --- NSFW routing at categorize time ---------------------------------------------


def test_nsfw_meme_routed_at_categorize_time_when_missed_at_process(
    tmp_path, settings, fake_ollama, patch_ollama, quiet_console
):
    # NSFW disabled during `process`, so the flagged meme stays uncategorised at the root.
    settings.categorize.nsfw.enabled = False
    patch_ollama(fake_ollama)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    for i in range(12):
        make_jpeg(
            input_dir / f"m{i:02d}.jpg", color=((i * 37) % 256, (i * 61) % 256, (i * 89) % 256)
        )
    make_jpeg(input_dir / "risque.jpg", color=(250, 10, 10))
    fake_ollama.analysis_queue = [default_analysis(f"cat item {i}") for i in range(12)] + [
        {**default_analysis("risque meme"), "nsfw": True}
    ]
    output_dir = tmp_path / "out"
    process_collection(input_dir, output_dir, settings, workers=1, console=quiet_console)
    assert (output_dir / "risque-meme.json").is_file()  # still flat at root

    # Now enable the rule and categorize: it must be auto-routed, excluded from discovery.
    settings.categorize.nsfw.enabled = True
    summary = categorize_collection(output_dir, settings, console=quiet_console)

    assert summary.categorized == 13  # 12 discovered + 1 nsfw-routed
    nsfw_files = list((output_dir / "00 - NSFW" / "image").glob("*.jpg"))
    assert len(nsfw_files) == 1
    sidecar = Sidecar.read(nsfw_files[0].with_suffix(".json"))
    assert sidecar.category.id == 0
    assert sidecar.category.source == "rule"

    cf = CategoriesFile.read(output_dir / "categories.json")
    nsfw_cat = next(c for c in cf.categories if c.id == 0)
    assert nsfw_cat.folder == "00 - NSFW"
    assert nsfw_cat.count == 1
    # the 12 ordinary memes were still discovered/assigned normally
    assert len(cf.categories) == 11  # 9 discovered + others (10) + nsfw (0)


# --- `migrate`: standalone v1 -> v2 layout normalisation, no LLM calls -----------


def _write_v1_sidecar(path, *, id_, sha, title) -> None:
    """A hand-built v1-shaped sidecar JSON (no media_type/pinned/source/layout_version)."""
    data = {
        "schema_version": 1,
        "id": id_,
        "file": {
            "name": path.with_suffix(".jpg").name,
            "width": 10,
            "height": 10,
            "bytes": 100,
            "format": "jpeg",
            "animated": False,
        },
        "source": {
            "original_filename": "src.jpg",
            "original_path": "C:/memes/src.jpg",
            "sha256": sha,
            "format": "jpeg",
            "width": 10,
            "height": 10,
            "bytes": 90,
            "converted": False,
        },
        "analysis": {
            "title": title,
            "description": "A test meme.",
            "ocr_text": "",
            "tags": ["a"],
            "subjects": [],
            "meme_type": "reaction",
            "template": None,
            "tone": ["funny"],
            "topics": ["general"],
            "language": "none",
            "nsfw": False,
            "confidence": 0.5,
        },
        "category": {
            "id": 1,
            "name": "reaction",
            "confidence": 0.9,
            "assigned_at": "2026-01-01T00:00:00Z",
            "model": "qwen3.5:4b",
            "run_id": "abc123def456",
        },
        "processing": {
            "tool_version": "0.1.0",
            "profile": "fast",
            "vision_model": "qwen3.5:4b",
            "prompt_version": 1,
            "processed_at": "2026-01-01T00:00:00Z",
            "duration_s": 1.0,
            "image_sent": {"width": 10, "height": 10, "tiles": 1},
        },
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_migrate_converts_v1_layout_idempotently_and_reads_v1_sidecars(tmp_path, settings):
    from PIL import Image

    output_dir = tmp_path / "out"
    cat_dir = output_dir / "01-reaction"
    cat_dir.mkdir(parents=True)
    Image.new("RGB", (10, 10), (1, 2, 3)).save(cat_dir / "old-meme.jpg", format="JPEG")
    _write_v1_sidecar(cat_dir / "old-meme.json", id_="0" * 16, sha="a" * 64, title="Old Meme")

    v1_categories = {
        "schema_version": 1,
        "generated_at": "2026-01-01T00:00:00Z",
        "model": "qwen3.5:9b",
        "profile": "accurate",
        "run_id": "abc123def456",
        "collection_size": 1,
        "categories": [
            {
                "id": 1,
                "name": "reaction",
                "folder": "01-reaction",
                "description": "",
                "keywords": [],
                "count": 1,
            }
        ],
    }
    (output_dir / "categories.json").write_text(
        json.dumps(v1_categories, indent=2), encoding="utf-8"
    )

    summary = migrate_collection(output_dir, settings)
    assert summary.moved == 1
    assert (cat_dir / "image" / "old-meme.jpg").is_file()
    assert (cat_dir / "image" / "old-meme.json").is_file()
    assert not (cat_dir / "old-meme.jpg").exists()

    # the v1 sidecar (schema_version 1, on disk) is rewritten in place, not just defaulted
    # in memory: the JSON file itself now says schema_version 2 with the v2 fields present.
    assert summary.sidecars_upgraded == 1
    raw = json.loads((cat_dir / "image" / "old-meme.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == 2
    assert raw["file"]["media_type"] == "image"

    sidecar = Sidecar.read(cat_dir / "image" / "old-meme.json")
    assert sidecar.schema_version == 2
    assert sidecar.file.media_type == "image"
    assert sidecar.category.source == "llm"

    cf = CategoriesFile.read(output_dir / "categories.json")
    assert cf.layout_version == 2
    reaction_cat = next(c for c in cf.categories if c.id == 1)
    assert reaction_cat.pinned is False
    # nsfw enabled by default -> registered too, adding a second (pinned) category entry
    assert any(c.id == 0 and c.pinned for c in cf.categories)

    # idempotent: running again moves nothing and upgrades nothing further
    second = migrate_collection(output_dir, settings)
    assert second.moved == 0
    assert second.sidecars_upgraded == 0
    assert (cat_dir / "image" / "old-meme.jpg").is_file()


def test_migrate_registers_nsfw_category_only_when_enabled(tmp_path, settings):
    output_dir = tmp_path / "out"
    output_dir.mkdir()

    settings.categorize.nsfw.enabled = False
    summary = migrate_collection(output_dir, settings)
    assert summary.categories_created == []
    assert not (output_dir / "categories.json").exists()

    settings.categorize.nsfw.enabled = True
    summary = migrate_collection(output_dir, settings)
    assert [c.folder for c in summary.categories_created] == ["00 - NSFW"]
    cf = CategoriesFile.read(output_dir / "categories.json")
    nsfw_cat = next(c for c in cf.categories if c.id == 0)
    assert nsfw_cat.folder == "00 - NSFW"
    assert nsfw_cat.pinned is True
    assert nsfw_cat.rule == "nsfw"

    # idempotent: running again doesn't re-create it
    summary2 = migrate_collection(output_dir, settings)
    assert summary2.categories_created == []


def test_migrate_adopts_existing_nsfw_folder_and_normalises_its_layout(tmp_path, settings):
    """A v1-era `00 - NSFW` folder created by hand (or an older run) is adopted - migrate
    registers it as the pinned category and normalises its layout, never creating a second
    folder for it."""
    from PIL import Image

    output_dir = tmp_path / "out"
    nsfw_dir = output_dir / "00 - NSFW"
    nsfw_dir.mkdir(parents=True)
    Image.new("RGB", (10, 10), (9, 9, 9)).save(nsfw_dir / "old-nsfw-meme.jpg", format="JPEG")
    _write_v1_sidecar(
        nsfw_dir / "old-nsfw-meme.json", id_="1" * 16, sha="b" * 64, title="Old Nsfw Meme"
    )

    summary = migrate_collection(output_dir, settings)
    assert [c.folder for c in summary.categories_created] == ["00 - NSFW"]
    assert summary.moved == 1
    assert (nsfw_dir / "image" / "old-nsfw-meme.jpg").is_file()
    assert not (output_dir / "00-nsfw").exists()  # no second, slug-named folder created
