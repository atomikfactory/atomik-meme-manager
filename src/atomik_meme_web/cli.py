"""`atomik-meme-web` console script: serve (the default, bare-invocation, action) and `index`."""

from __future__ import annotations

import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import typer
import uvicorn
from rich.console import Console

from atomik_meme_web.api import create_app
from atomik_meme_web.config import WebConfig, WebConfigError, load_web_config
from atomik_meme_web.db import Database
from atomik_meme_web.indexer import Indexer
from atomik_meme_web.library_state import resolve_startup_library
from atomik_meme_web.services import resolve_data_dir_for_library

app = typer.Typer(
    add_completion=False,
    help="Serve (or one-off index) a local atomik-meme media library over HTTP.",
)
console = Console()

_LIBRARY_OPTION = typer.Option(
    None, "--library", help="Folder to index (default: remembered/configured library)"
)
_DATA_DIR_OPTION = typer.Option(None, "--data-dir", help="Where index.db/thumbs are stored")
_CONFIG_OPTION = typer.Option(None, "--config", help="Path to atomik-meme.yaml")
_HOST_OPTION = typer.Option(None, "--host")
_PORT_OPTION = typer.Option(None, "--port")
_NO_BROWSER_OPTION = typer.Option(False, "--no-browser", help="Don't open a browser tab")
_NO_SCAN_OPTION = typer.Option(False, "--no-scan", help="Skip the scan-on-start")


def _load_config_or_exit(
    config: Path | None,
    data_dir: Path | None,
    host: str | None = None,
    port: int | None = None,
    open_browser: bool | None = None,
    scan_on_start: bool | None = None,
) -> WebConfig:
    """Load the `web:` config. Library resolution is deliberately separate (via
    `resolve_startup_library`): `serve` and `index` disagree on whether "no library found" is
    fatal, so it can't be baked into this shared loader."""
    try:
        return load_web_config(
            config,
            data_dir=data_dir,
            host=host,
            port=port,
            open_browser=open_browser,
            scan_on_start=scan_on_start,
        )
    except WebConfigError as exc:
        console.print(f"[red]Config error:[/] {exc}")
        raise typer.Exit(code=1) from exc


def _open_browser_when_ready(url: str, health_url: str, timeout_s: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(health_url, timeout=0.5)  # noqa: S310
            webbrowser.open(url)
            return
        except Exception:  # noqa: BLE001 - keep polling until the server is up
            time.sleep(0.15)


def _serve(
    library: Path | None,
    data_dir: Path | None,
    host: str | None,
    port: int | None,
    no_browser: bool,
    no_scan: bool,
    config: Path | None,
) -> None:
    cfg = _load_config_or_exit(
        config,
        data_dir,
        host=host,
        port=port,
        open_browser=(False if no_browser else None),
        scan_on_start=(False if no_scan else None),
    )
    resolved_library = resolve_startup_library(cfg, cli_library=library)
    if library is not None and not resolved_library.is_dir():
        # Only the explicit --library flag is a hard error; the other precedence sources
        # (remembered library, web.library, ./output) already skip themselves when missing,
        # falling through to no-library mode instead.
        console.print(f"[red]Library directory not found:[/] {resolved_library}")
        raise typer.Exit(code=1)

    fastapi_app = create_app(cfg, library=resolved_library)

    if cfg.open_browser:
        browser_host = "127.0.0.1" if cfg.host in ("0.0.0.0", "::") else cfg.host  # noqa: S104
        url = f"http://{browser_host}:{cfg.port}/"
        health_url = f"http://{browser_host}:{cfg.port}/api/health"
        threading.Thread(
            target=_open_browser_when_ready, args=(url, health_url), daemon=True
        ).start()

    if resolved_library is not None:
        console.print(f"Serving [bold]{resolved_library}[/] at http://{cfg.host}:{cfg.port}/")
        console.print(f"Data dir: {resolve_data_dir_for_library(cfg, resolved_library)}")
    else:
        console.print(
            f"No library selected yet -- choose one in the UI at "
            f"http://{cfg.host}:{cfg.port}/ (Ctrl+O)"
        )
    uvicorn.run(fastapi_app, host=cfg.host, port=cfg.port, log_level="info")


@app.command()
def serve(
    library: Path | None = _LIBRARY_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
    host: str | None = _HOST_OPTION,
    port: int | None = _PORT_OPTION,
    no_browser: bool = _NO_BROWSER_OPTION,
    no_scan: bool = _NO_SCAN_OPTION,
    config: Path | None = _CONFIG_OPTION,
) -> None:
    """Serve the API + SPA."""
    _serve(library, data_dir, host, port, no_browser, no_scan, config)


@app.command(name="index")
def index_cmd(
    library: Path | None = _LIBRARY_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
    config: Path | None = _CONFIG_OPTION,
    rebuild: bool = typer.Option(
        False,
        "--rebuild",
        help=(
            "Drop and rebuild items/items_fts/item_tags from a full scan before indexing "
            "(favorites/views/collections are untouched); a manual escape hatch if the derived "
            "tables ever drift out of sync with the library"
        ),
    ),
) -> None:
    """One-off scan: prints added/modified/removed/moved and exits."""
    cfg = _load_config_or_exit(config, data_dir)
    resolved_library = resolve_startup_library(cfg, cli_library=library)
    if resolved_library is None:
        console.print(
            "[red]No library found.[/] Pass --library, set web.library in atomik-meme.yaml, "
            "or run 'atomik-meme-web' and choose one from the UI."
        )
        raise typer.Exit(code=1)
    if not resolved_library.is_dir():
        console.print(f"[red]Library directory not found:[/] {resolved_library}")
        raise typer.Exit(code=1)

    db = Database(resolve_data_dir_for_library(cfg, resolved_library) / "index.db")
    if rebuild:
        console.print("Rebuilding items/items_fts/item_tags before scanning...")
        db.rebuild_derived_tables()
    indexer = Indexer(db, resolved_library, cfg)
    try:
        record = indexer.scan()
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Scan failed:[/] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(
        f"added={record.added} modified={record.modified} "
        f"removed={record.removed} moved={record.moved} "
        f"total={record.total} duration_s={record.duration_s:.2f}"
    )
    raise typer.Exit(code=0)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    library: Path | None = _LIBRARY_OPTION,
    data_dir: Path | None = _DATA_DIR_OPTION,
    host: str | None = _HOST_OPTION,
    port: int | None = _PORT_OPTION,
    no_browser: bool = _NO_BROWSER_OPTION,
    no_scan: bool = _NO_SCAN_OPTION,
    config: Path | None = _CONFIG_OPTION,
) -> None:
    """Bare `atomik-meme-web` (no subcommand) is equivalent to `atomik-meme-web serve`."""
    if ctx.invoked_subcommand is None:
        _serve(library, data_dir, host, port, no_browser, no_scan, config)


if __name__ == "__main__":
    app()
