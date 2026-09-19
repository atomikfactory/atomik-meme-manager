import json
import logging

from typer.testing import CliRunner

from atomik_meme.cli import app
from tests.conftest import default_analysis, default_discovery, make_animated_gif, make_jpeg

runner = CliRunner()


def test_help_lists_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in ("process", "categorize", "run", "check", "search", "init-config", "models"):
        assert name in result.output


def test_init_config_writes_loadable_file(tmp_path):
    cfg_path = tmp_path / "cfg.yaml"
    result = runner.invoke(app, ["init-config", str(cfg_path)])
    assert result.exit_code == 0
    assert cfg_path.is_file()

    from atomik_meme.config import load_settings

    settings = load_settings(cfg_path)
    assert settings.ollama.host == "http://127.0.0.1:11434"
    assert settings.image.model_max_pixels == 1_200_000


def test_check_exit_code_2_when_server_unreachable(tmp_path):
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text("ollama:\n  host: http://127.0.0.1:1\n  timeout_s: 3\n", encoding="utf-8")
    result = runner.invoke(app, ["--config", str(cfg_path), "check"])
    assert result.exit_code == 2
    assert "unreachable" in result.output.lower() or "cannot reach" in result.output.lower()


def test_process_dry_run_lists_files_and_calls_no_model(tmp_path, monkeypatch):
    def _boom(**kwargs):
        raise AssertionError("must not construct OllamaClient during --dry-run")

    monkeypatch.setattr("atomik_meme.processor.OllamaClient", _boom)

    input_dir = tmp_path / "in"
    input_dir.mkdir()
    make_jpeg(input_dir / "a.jpg", color=(10, 20, 30))
    make_jpeg(input_dir / "b.jpg", color=(200, 210, 220))
    output_dir = tmp_path / "out"

    result = runner.invoke(app, ["process", str(input_dir), str(output_dir), "--dry-run"])
    assert result.exit_code == 0
    assert "a.jpg" in result.output
    assert "b.jpg" in result.output
    assert "Would process: 2" in result.output


def test_process_missing_input_dir_is_a_usage_error(tmp_path):
    result = runner.invoke(
        app, ["process", str(tmp_path / "does-not-exist"), str(tmp_path / "out")]
    )
    assert result.exit_code == 1


def test_process_prints_summary_and_reports_failures(tmp_path, monkeypatch, fake_ollama):
    monkeypatch.setattr("atomik_meme.processor.OllamaClient", lambda **kwargs: fake_ollama)

    input_dir = tmp_path / "in"
    input_dir.mkdir()
    make_jpeg(input_dir / "good.jpg")
    (input_dir / "bad.jpg").write_bytes(b"not-an-image")
    output_dir = tmp_path / "out"

    result = runner.invoke(app, ["process", str(input_dir), str(output_dir)])
    assert result.exit_code == 3  # batch completed with failures
    assert "Processed: 1" in result.output
    assert "Failed: 1" in result.output
    assert "Failures:" in result.output
    assert "bad.jpg" in result.output
    assert "decode:" in result.output
    assert "Run summary:" in result.output


def test_process_then_categorize_end_to_end(tmp_path, monkeypatch, fake_ollama):
    monkeypatch.setattr("atomik_meme.processor.OllamaClient", lambda **kwargs: fake_ollama)
    monkeypatch.setattr("atomik_meme.categorizer.OllamaClient", lambda **kwargs: fake_ollama)

    input_dir = tmp_path / "in"
    input_dir.mkdir()
    for i in range(10):
        make_jpeg(input_dir / f"m{i}.jpg", color=((i * 20) % 256, 10, 10))
    fake_ollama.analysis_queue = [default_analysis(f"item {i}") for i in range(10)]
    output_dir = tmp_path / "out"

    result = runner.invoke(app, ["process", str(input_dir), str(output_dir)])
    assert result.exit_code == 0
    assert "Processed: 10" in result.output

    result2 = runner.invoke(app, ["categorize", str(output_dir)])
    assert result2.exit_code == 0
    assert (output_dir / "categories.json").is_file()
    assert "Categorized: 10" in result2.output


def test_search_json_output(tmp_path, monkeypatch, fake_ollama):
    monkeypatch.setattr("atomik_meme.processor.OllamaClient", lambda **kwargs: fake_ollama)

    input_dir = tmp_path / "in"
    input_dir.mkdir()
    make_jpeg(input_dir / "one.jpg")
    output_dir = tmp_path / "out"
    assert runner.invoke(app, ["process", str(input_dir), str(output_dir)]).exit_code == 0

    result = runner.invoke(app, ["search", str(output_dir), "test", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["analysis"]["title"]


def test_run_command_processes_then_categorizes(tmp_path, monkeypatch, fake_ollama):
    monkeypatch.setattr("atomik_meme.processor.OllamaClient", lambda **kwargs: fake_ollama)
    monkeypatch.setattr("atomik_meme.categorizer.OllamaClient", lambda **kwargs: fake_ollama)

    input_dir = tmp_path / "in"
    input_dir.mkdir()
    for i in range(10):
        make_jpeg(input_dir / f"m{i}.jpg", color=((i * 20) % 256, 30, 40))
    fake_ollama.analysis_queue = [default_analysis(f"run item {i}") for i in range(10)]
    output_dir = tmp_path / "out"

    result = runner.invoke(app, ["run", str(input_dir), str(output_dir)])
    assert result.exit_code == 0
    assert "Processed: 10" in result.output
    assert "Categorized: 10" in result.output
    assert (output_dir / "categories.json").is_file()


def test_workers_zero_is_a_usage_error(tmp_path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    make_jpeg(input_dir / "a.jpg")
    output_dir = tmp_path / "out"

    result = runner.invoke(
        app, ["process", str(input_dir), str(output_dir), "--workers", "0", "--dry-run"]
    )
    assert result.exit_code == 1

    output_dir.mkdir()
    result2 = runner.invoke(app, ["categorize", str(output_dir), "--workers", "0"])
    assert result2.exit_code == 1


def test_process_categorize_flag_exit_code_when_categorize_fails(
    tmp_path, monkeypatch, fake_ollama
):
    monkeypatch.setattr("atomik_meme.processor.OllamaClient", lambda **kwargs: fake_ollama)
    monkeypatch.setattr("atomik_meme.categorizer.OllamaClient", lambda **kwargs: fake_ollama)

    input_dir = tmp_path / "in"
    input_dir.mkdir()
    for i in range(3):
        make_jpeg(input_dir / f"m{i}.jpg", color=((i * 40) % 256, 5, 5))
    fake_ollama.analysis_queue = [default_analysis(f"item {i}") for i in range(3)]
    output_dir = tmp_path / "out"

    # Discovery keeps returning the wrong number of categories -> exhausts retries -> raises
    # CategorizeError. `process --categorize` must map that to exit 3, not silently exit 0.
    fake_ollama.discovery_queue = [
        {"categories": default_discovery(3)["categories"]} for _ in range(10)
    ]

    result = runner.invoke(app, ["process", str(input_dir), str(output_dir), "--categorize"])
    assert result.exit_code == 3
    assert "Processed: 3" in result.output


def test_init_config_then_load_settings_produces_no_unknown_key_warnings(tmp_path, caplog):
    from atomik_meme.config import load_settings

    cfg_path = tmp_path / "cfg.yaml"
    result = runner.invoke(app, ["init-config", str(cfg_path)])
    assert result.exit_code == 0

    with caplog.at_level(logging.WARNING):
        load_settings(cfg_path)
    unknown = [r for r in caplog.records if "Unknown config key" in r.message]
    assert unknown == []


def test_check_reports_ffmpeg_line_even_when_ollama_unreachable(tmp_path):
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text("ollama:\n  host: http://127.0.0.1:1\n  timeout_s: 3\n", encoding="utf-8")
    result = runner.invoke(app, ["--config", str(cfg_path), "check"])
    assert result.exit_code == 2
    assert "ffmpeg:" in result.output
    assert "ffprobe:" in result.output


def test_migrate_command_smoke(tmp_path, monkeypatch, fake_ollama):
    monkeypatch.setattr("atomik_meme.processor.OllamaClient", lambda **kwargs: fake_ollama)
    monkeypatch.setattr("atomik_meme.categorizer.OllamaClient", lambda **kwargs: fake_ollama)

    input_dir = tmp_path / "in"
    input_dir.mkdir()
    for i in range(10):
        make_jpeg(input_dir / f"m{i}.jpg", color=((i * 20) % 256, 10, 10))
    fake_ollama.analysis_queue = [default_analysis(f"item {i}") for i in range(10)]
    output_dir = tmp_path / "out"
    assert runner.invoke(app, ["process", str(input_dir), str(output_dir)]).exit_code == 0
    assert runner.invoke(app, ["categorize", str(output_dir)]).exit_code == 0

    result = runner.invoke(app, ["migrate", str(output_dir)])
    assert result.exit_code == 0
    assert "Run summary:" in result.output


def test_migrate_missing_output_dir_is_a_usage_error(tmp_path):
    result = runner.invoke(app, ["migrate", str(tmp_path / "does-not-exist")])
    assert result.exit_code == 1


def test_search_media_filter_and_column(tmp_path, monkeypatch, fake_ollama):
    monkeypatch.setattr("atomik_meme.processor.OllamaClient", lambda **kwargs: fake_ollama)

    input_dir = tmp_path / "in"
    input_dir.mkdir()
    make_jpeg(input_dir / "one.jpg")
    make_animated_gif(input_dir / "anim.gif")
    output_dir = tmp_path / "out"
    assert runner.invoke(app, ["process", str(input_dir), str(output_dir)]).exit_code == 0

    result = runner.invoke(app, ["search", str(output_dir), "", "--media", "gif", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 1
    assert data[0]["file"]["media_type"] == "gif"

    table_result = runner.invoke(app, ["search", str(output_dir), ""])
    assert table_result.exit_code == 0
    assert "Media" in table_result.output


def test_summary_lines_are_separate_and_categories_created_is_never_null(
    tmp_path, monkeypatch, fake_ollama
):
    """Regression: "Hinted: N" used to be concatenated onto the end of the main counts line,
    which could wrap mid-fragment on a narrow terminal (a lone "1" landing on its own visual
    line in a live run). Each summary piece must be its own printed line, in the fixed
    order (counts first, `Run summary:` last) - and the run-summary JSON's `categories_created`
    must be a real list of `{id, name, folder}`, never null, even though `RunSummary` itself
    never sets it to None (this guards the actual JSON on disk, not just the Python default)."""
    monkeypatch.setattr("atomik_meme.processor.OllamaClient", lambda **kwargs: fake_ollama)

    input_dir = tmp_path / "in"
    input_dir.mkdir()
    make_jpeg(input_dir / "one.jpg")
    output_dir = tmp_path / "out"

    result = runner.invoke(
        app, ["process", str(input_dir), str(output_dir), "--category", "Test Pinned"]
    )
    assert result.exit_code == 0
    lines = [ln.strip() for ln in result.output.splitlines() if ln.strip()]

    assert "Hinted: 1" in lines, f"expected a standalone 'Hinted: 1' line, got: {lines}"

    created_lines = [ln for ln in lines if ln.startswith("Created categories:")]
    assert created_lines, f"expected a 'Created categories:' line, got: {lines}"
    assert "11-test-pinned" in created_lines[0]

    # "Run summary:" prints last - nothing from the counts section may follow it. (Its own text
    # can still visually wrap onto a further line if the tmp path is long; that's an unrelated,
    # unavoidable terminal-width effect on a long single value, not the bug being guarded here.)
    run_summary_idx = next(i for i, ln in enumerate(lines) if ln.startswith("Run summary:"))
    assert all(
        not ln.startswith(("Processed:", "Hinted:", "Created categories:", "Removed from input:"))
        for ln in lines[run_summary_idx + 1 :]
    )

    summary_path = next((output_dir / ".meme-manager" / "runs").glob("*-process.json"))
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    assert data["categories_created"] is not None
    assert {c["id"] for c in data["categories_created"]} == {0, 11}  # nsfw (0) + hint (11)
    for entry in data["categories_created"]:
        assert set(entry) == {"id", "name", "folder"}
