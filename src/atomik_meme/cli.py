"""Typer CLI: commands, summary rendering, and exit codes."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress
from rich.table import Table

from atomik_meme import media as media_module
from atomik_meme.categories import migrate_collection
from atomik_meme.categorizer import CategorizeError, categorize_collection
from atomik_meme.config import Settings, SettingsError, load_settings, write_sample_config
from atomik_meme.index import run_summary_path
from atomik_meme.logging_setup import (
    add_file_handler,
    parse_level,
    reconfigure_streams_utf8,
    setup_logging,
)
from atomik_meme.ollama_client import ModelMissing, OllamaClient, OllamaError, OllamaUnavailable
from atomik_meme.processor import process_collection
from atomik_meme.schema import RunSummary
from atomik_meme.search import search_collection

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Analyse, name, and categorise meme image collections using a local Ollama vision model.",
)
models_app = typer.Typer(help="Inspect or pull Ollama models.")
app.add_typer(models_app, name="models")

console = Console()


OLLAMA_HINT = (
    "[dim]Hint: is the Ollama server running? For the repo-local instance run "
    r"scripts\ollama-serve-local.ps1 and leave it open; otherwise start the Ollama app."
)


def _print_ollama_error(exc: Exception) -> None:
    """Print an Ollama connection/model error, plus a start-up hint when the server is down."""
    console.print(f"[red]{exc}[/]")
    if isinstance(exc, OllamaUnavailable):
        console.print(OLLAMA_HINT)


EXIT_OK = 0
EXIT_USAGE = 1
EXIT_OLLAMA = 2
EXIT_FAILURES = 3


class _AppState:
    config_path: Path | None = None
    verbose: bool = False
    log_file: Path | None = None


_state = _AppState()


@app.callback()
def main(
    ctx: typer.Context,
    config: Path | None = typer.Option(None, "--config", help="Path to atomik-meme.yaml"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose (INFO) console logging"),
    log_file: Path | None = typer.Option(
        None, "--log-file", help="Write logs to this file regardless of config"
    ),
) -> None:
    reconfigure_streams_utf8()
    _state.config_path = config
    _state.verbose = verbose
    _state.log_file = log_file
    setup_logging(verbose, log_file, console=console)


def _load_settings_or_exit() -> Settings:
    try:
        settings = load_settings(_state.config_path)
    except SettingsError as exc:
        console.print(f"[red]Config error:[/] {exc}")
        raise typer.Exit(code=EXIT_USAGE) from exc
    # `logging.level` is only known once settings are loaded; re-apply it now (root logger
    # level, and the level of an already-attached --log-file handler, if any). Console level
    # (-v / WARNING) is unaffected - see logging_setup.setup_logging.
    setup_logging(_state.verbose, _state.log_file, console=console, level=settings.logging.level)
    return settings


def _maybe_attach_file_logger(settings: Settings, output_dir: Path) -> None:
    if _state.log_file is not None:
        return
    if settings.logging.file:
        add_file_handler(
            Path(output_dir) / ".meme-manager" / "logs" / "atomik-meme.log",
            level=parse_level(settings.logging.level),
        )


def _require_dir(path: Path, label: str) -> None:
    if not path.is_dir():
        console.print(f"[red]{label} directory not found:[/] {path}")
        raise typer.Exit(code=EXIT_USAGE)


def _require_positive_workers(workers: int | None) -> None:
    if workers is not None and workers < 1:
        console.print(f"[red]--workers must be >= 1, got {workers}[/]")
        raise typer.Exit(code=EXIT_USAGE)


def _make_client(settings: Settings) -> OllamaClient:
    return OllamaClient(
        host=settings.ollama.host,
        timeout_s=settings.ollama.timeout_s,
        retries=settings.ollama.retries,
        keep_alive=settings.ollama.keep_alive,
    )


def _print_summary(
    summary: RunSummary, path: Path | None, extra_categorized: int | None = None
) -> None:
    """Print the run summary in the fixed order: the main counts line
    first, then any detail lines (Hinted/Created categories/Removed from input/Failures), and
    `Run summary:` always last. Each piece is its own `console.print()` call - concatenating
    everything onto one long line risks Rich wrapping mid-fragment on a narrow terminal (e.g. a
    lone "1" from "Hinted: 1" landing on its own visual line).
    """
    skip_total = summary.skipped_done + summary.skipped_duplicate
    line = (
        f"Processed: {summary.processed}   "
        f"Skipped: {skip_total} ({summary.skipped_done} already done, "
        f"{summary.skipped_duplicate} duplicates)   "
        f"Failed: {summary.failed}"
    )
    categorized = (
        extra_categorized if extra_categorized is not None else (summary.categorized or None)
    )
    if categorized is not None:
        line += f"   Categorized: {categorized}"
    console.print(line)
    console.print(f"Hinted: {summary.hinted}")
    if summary.categories_created:
        folders = ", ".join(c.folder for c in summary.categories_created)
        console.print(f"Created categories: {folders}")
    if summary.consume:
        console.print(f"Removed from input: {summary.removed_from_input}")
    if summary.failures:
        console.print("Failures:")
        for f in summary.failures:
            name = Path(f.source_path).name
            console.print(f"- {name:<28} {f.stage}: {f.message}")
    if path is not None:
        console.print(f"Run summary: {path.as_posix()}")


@app.command()
def process(
    input: Path = typer.Argument(..., help="Input directory of meme images"),
    output: Path = typer.Argument(..., help="Output directory"),
    model: str | None = typer.Option(None, "--model", help="Profile: fast|accurate"),
    vision_model: str | None = typer.Option(
        None, "--vision-model", help="Exact model tag override"
    ),
    workers: int | None = typer.Option(None, "--workers"),
    force: bool = typer.Option(False, "--force", help="Reprocess sources already in the index"),
    limit: int | None = typer.Option(None, "--limit", help="Only process the first N files"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    recursive: bool | None = typer.Option(None, "--recursive/--no-recursive"),
    categorize: bool = typer.Option(False, "--categorize", help="Also run categorize afterwards"),
    ocr_pass: str | None = typer.Option(
        None, "--ocr-pass", help="off|fallback|always - overrides processing.ocr_pass"
    ),
    category: str | None = typer.Option(
        None, "--category", help="Hint: assign every file this run directly to this category"
    ),
    consume: bool | None = typer.Option(
        None, "--consume/--no-consume", help="Delete verified sources from INPUT after writing"
    ),
) -> None:
    """Analyse INPUT and write named, tagged copies (plus JSON sidecars) into OUTPUT."""
    settings = _load_settings_or_exit()
    _require_dir(input, "Input")
    _require_positive_workers(workers)
    if recursive is not None:
        settings.processing.recursive = recursive
    profile = model or settings.ollama.default_profile
    if not dry_run:
        _maybe_attach_file_logger(settings, output)

    try:
        summary = process_collection(
            input_dir=input,
            output_dir=output,
            settings=settings,
            profile=profile,
            vision_model_override=vision_model,
            workers=workers,
            force=force,
            limit=limit,
            dry_run=dry_run,
            console=console,
            ocr_pass_override=ocr_pass,
            category_hint=category,
            consume=consume,
        )
    except (OllamaUnavailable, ModelMissing) as exc:
        _print_ollama_error(exc)
        raise typer.Exit(code=EXIT_OLLAMA) from exc

    if dry_run:
        raise typer.Exit(code=EXIT_OK)

    cat_summary = None
    cat_failed = False
    if categorize:
        try:
            cat_summary = categorize_collection(
                output_dir=output, settings=settings, profile=profile, console=console
            )
        except (OllamaUnavailable, ModelMissing) as exc:
            _print_ollama_error(exc)
            raise typer.Exit(code=EXIT_OLLAMA) from exc
        except CategorizeError as exc:
            console.print(f"[red]{exc}[/]")
            cat_failed = True

    _print_summary(
        summary,
        run_summary_path(output, summary),
        extra_categorized=(cat_summary.categorized if cat_summary else None),
    )
    if cat_summary is not None:
        console.print(f"Categorize summary: {run_summary_path(output, cat_summary).as_posix()}")

    failed = summary.failed > 0 or cat_failed
    raise typer.Exit(code=EXIT_FAILURES if failed else EXIT_OK)


@app.command()
def categorize(
    output: Path = typer.Argument(..., help="Output directory (previously produced by `process`)"),
    model: str | None = typer.Option(None, "--model", help="Profile: fast|accurate"),
    text_model: str | None = typer.Option(None, "--text-model", help="Exact model tag override"),
    rediscover: bool = typer.Option(
        False, "--rediscover", help="Re-run discovery, reassign everything"
    ),
    dry_run: bool = typer.Option(False, "--dry-run"),
    workers: int | None = typer.Option(None, "--workers"),
) -> None:
    """Group memes in OUTPUT into (at most 10) category folders."""
    settings = _load_settings_or_exit()
    _require_dir(output, "Output")
    _require_positive_workers(workers)
    profile = model or settings.ollama.default_profile
    if not dry_run:
        _maybe_attach_file_logger(settings, output)

    try:
        summary = categorize_collection(
            output_dir=output,
            settings=settings,
            profile=profile,
            text_model_override=text_model,
            rediscover=rediscover,
            dry_run=dry_run,
            workers=workers,
            console=console,
        )
    except (OllamaUnavailable, ModelMissing) as exc:
        _print_ollama_error(exc)
        raise typer.Exit(code=EXIT_OLLAMA) from exc
    except CategorizeError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=EXIT_FAILURES) from exc

    if dry_run:
        raise typer.Exit(code=EXIT_OK)

    console.print(f"Categorized: {summary.categorized}")
    console.print(f"Run summary: {run_summary_path(output, summary).as_posix()}")
    raise typer.Exit(code=EXIT_OK)


@app.command(name="run")
def run_cmd(
    input: Path = typer.Argument(..., help="Input directory of meme images"),
    output: Path = typer.Argument(..., help="Output directory"),
    model: str | None = typer.Option(None, "--model", help="Profile: fast|accurate"),
    vision_model: str | None = typer.Option(None, "--vision-model"),
    text_model: str | None = typer.Option(None, "--text-model"),
    workers: int | None = typer.Option(None, "--workers"),
    force: bool = typer.Option(False, "--force"),
    limit: int | None = typer.Option(None, "--limit"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    recursive: bool | None = typer.Option(None, "--recursive/--no-recursive"),
    rediscover: bool = typer.Option(False, "--rediscover"),
    ocr_pass: str | None = typer.Option(
        None, "--ocr-pass", help="off|fallback|always - overrides processing.ocr_pass"
    ),
    category: str | None = typer.Option(
        None, "--category", help="Hint: assign every file this run directly to this category"
    ),
    consume: bool | None = typer.Option(
        None, "--consume/--no-consume", help="Delete verified sources from INPUT after writing"
    ),
) -> None:
    """`process` then `categorize` in one step."""
    settings = _load_settings_or_exit()
    _require_dir(input, "Input")
    _require_positive_workers(workers)
    if recursive is not None:
        settings.processing.recursive = recursive
    profile = model or settings.ollama.default_profile
    if not dry_run:
        _maybe_attach_file_logger(settings, output)

    try:
        process_summary = process_collection(
            input_dir=input,
            output_dir=output,
            settings=settings,
            profile=profile,
            vision_model_override=vision_model,
            workers=workers,
            force=force,
            limit=limit,
            dry_run=dry_run,
            console=console,
            ocr_pass_override=ocr_pass,
            category_hint=category,
            consume=consume,
        )
    except (OllamaUnavailable, ModelMissing) as exc:
        _print_ollama_error(exc)
        raise typer.Exit(code=EXIT_OLLAMA) from exc

    if dry_run:
        raise typer.Exit(code=EXIT_OK)

    cat_summary = None
    cat_failed = False
    try:
        cat_summary = categorize_collection(
            output_dir=output,
            settings=settings,
            profile=profile,
            text_model_override=text_model,
            rediscover=rediscover,
            dry_run=False,
            workers=workers,
            console=console,
        )
    except (OllamaUnavailable, ModelMissing) as exc:
        _print_ollama_error(exc)
        raise typer.Exit(code=EXIT_OLLAMA) from exc
    except CategorizeError as exc:
        console.print(f"[red]{exc}[/]")
        cat_failed = True

    _print_summary(
        process_summary,
        run_summary_path(output, process_summary),
        extra_categorized=(cat_summary.categorized if cat_summary else 0),
    )
    if cat_summary is not None:
        console.print(f"Categorize summary: {run_summary_path(output, cat_summary).as_posix()}")

    failed = process_summary.failed > 0 or cat_failed
    raise typer.Exit(code=EXIT_FAILURES if failed else EXIT_OK)


@app.command()
def check() -> None:
    """Check Ollama connectivity, model availability, effective config, and GPU."""
    settings = _load_settings_or_exit()
    console.print(f"Ollama host: {settings.ollama.host}")

    for line in media_module.ffmpeg_check_lines(settings.media):
        console.print(line)

    client = _make_client(settings)
    exit_code = EXIT_OK
    try:
        version = client.version()
        console.print(f"Ollama version: {version}")
    except OllamaUnavailable as exc:
        console.print(f"[red]Ollama unreachable:[/] {exc}")
        console.print(OLLAMA_HINT)
        console.print(f"Config source: {settings.source}")
        client.close()
        raise typer.Exit(code=EXIT_OLLAMA) from exc

    try:
        installed = client.list_models()
    except OllamaUnavailable:
        installed = []

    console.print("Models:")
    for role, models_cfg in (("vision", settings.models.vision), ("text", settings.models.text)):
        for profile_name in ("fast", "accurate"):
            spec = getattr(models_cfg, profile_name)
            present = spec.name in installed or f"{spec.name}:latest" in installed
            mark = "OK" if present else "MISSING"
            console.print(f"  {role}/{profile_name}: {spec.name}  [{mark}]")
            if not present:
                exit_code = EXIT_OLLAMA

    console.print(f"Config source: {settings.source}")

    gpu_lines = _gpu_info()
    if gpu_lines:
        console.print("GPU:")
        for line in gpu_lines:
            console.print(f"  {line}")

    client.close()
    raise typer.Exit(code=exit_code)


def _gpu_info() -> list[str] | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines or None


@models_app.command("list")
def models_list(profile: str = typer.Option("all", "--profile", help="fast|accurate|all")) -> None:
    settings = _load_settings_or_exit()
    client = _make_client(settings)
    try:
        installed = client.list_models()
    except OllamaUnavailable as exc:
        _print_ollama_error(exc)
        raise typer.Exit(code=EXIT_OLLAMA) from exc

    table = Table("Role", "Profile", "Model", "Installed")
    for role, models_cfg in (("vision", settings.models.vision), ("text", settings.models.text)):
        for profile_name in ("fast", "accurate"):
            if profile not in ("all", profile_name):
                continue
            spec = getattr(models_cfg, profile_name)
            present = spec.name in installed or f"{spec.name}:latest" in installed
            table.add_row(role, profile_name, spec.name, "yes" if present else "no")
    console.print(table)
    client.close()


@models_app.command("pull")
def models_pull(profile: str = typer.Option("all", "--profile", help="fast|accurate|all")) -> None:
    settings = _load_settings_or_exit()
    client = _make_client(settings)
    names: set[str] = set()
    for models_cfg in (settings.models.vision, settings.models.text):
        for profile_name in ("fast", "accurate"):
            if profile not in ("all", profile_name):
                continue
            names.add(getattr(models_cfg, profile_name).name)

    try:
        for name in sorted(names):
            console.print(f"Pulling [bold]{name}[/]...")
            with Progress(console=console) as progress:
                bar = progress.add_task(name, total=None)

                def _cb(data: dict, bar=bar, progress=progress, name=name) -> None:
                    progress.update(bar, description=f"{name}: {data.get('status', '')}")

                client.pull(name, progress_cb=_cb)
    except OllamaError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=EXIT_OLLAMA) from exc
    finally:
        client.close()


@app.command()
def search(
    output: Path = typer.Argument(..., help="Output directory to search"),
    query: str = typer.Argument("", help="Free-text query"),
    tag: list[str] = typer.Option([], "--tag", help="Require this tag (repeatable)"),
    category: str | None = typer.Option(None, "--category"),
    type: str | None = typer.Option(None, "--type", help="meme_type filter"),  # noqa: A002
    media: str | None = typer.Option(None, "--media", help="Filter: image|gif|video"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Search sidecar metadata under OUTPUT."""
    _require_dir(output, "Output")
    results = search_collection(
        output, query=query, tags=tag, category=category, meme_type=type, media_type=media
    )

    if json_output:
        # plain print(), not console.print(): Rich would soft-wrap long lines and corrupt the JSON
        print(json.dumps([r.model_dump(mode="json") for r in results], ensure_ascii=False))
        raise typer.Exit(code=EXIT_OK)

    table = Table("Name", "Media", "Category", "Title", "Tags")
    for r in results:
        cat_name = r.category.name if r.category else ""
        table.add_row(
            r.file.name, r.file.media_type, cat_name, r.analysis.title, ", ".join(r.analysis.tags)
        )
    console.print(table)
    raise typer.Exit(code=EXIT_OK)


@app.command()
def migrate(
    output: Path = typer.Argument(..., help="Output directory to normalise to the v2 layout"),
) -> None:
    """Normalise an existing OUTPUT to the `<category>/<image|gif|video>/` layout (no LLM calls)."""
    settings = _load_settings_or_exit()
    _require_dir(output, "Output")
    summary = migrate_collection(output_dir=output, settings=settings, console=console)
    console.print(f"Run summary: {run_summary_path(output, summary).as_posix()}")
    raise typer.Exit(code=EXIT_OK)


@app.command(name="init-config")
def init_config(
    path: Path = typer.Argument(Path("atomik-meme.yaml"), help="Where to write the sample config"),
) -> None:
    """Write the commented sample config file."""
    write_sample_config(path)
    console.print(f"Wrote sample config to {path}")
    raise typer.Exit(code=EXIT_OK)


if __name__ == "__main__":
    app()
