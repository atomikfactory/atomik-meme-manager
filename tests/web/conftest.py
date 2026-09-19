"""Shared fixtures for the atomik-meme-web backend test suite.

Tests build small synthetic libraries under `tmp_path` (jpg/gif/mp4 via the same helpers the
`atomik_meme` suite uses) and never touch the real `output`/`input` directories.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atomik_meme.schema import (
    Analysis,
    CategoryAssignment,
    FileInfo,
    ImageSent,
    ProcessingInfo,
    Sidecar,
    SourceInfo,
)
from atomik_meme_web.api import create_app
from atomik_meme_web.config import WebConfig
from atomik_meme_web.db import Database
from atomik_meme_web.indexer import Indexer
from tests.conftest import (  # noqa: F401 - re-exported for test modules
    has_ffmpeg,
    make_animated_gif,
    make_jpeg,
    make_mp4,
    make_static_gif,
)


def make_sidecar(
    media_path: Path,
    *,
    title: str = "Test Meme Title",
    description: str = "A test meme used for backend tests.",
    ocr_text: str = "HELLO WORLD",
    tags: list[str] | None = None,
    topics: list[str] | None = None,
    tone: list[str] | None = None,
    meme_type: str = "reaction",
    template: str | None = None,
    nsfw: bool = False,
    confidence: float = 0.9,
    category_name: str | None = None,
    category_id: int = 4,
) -> Path:
    """Write a `<stem>.json` sidecar beside `media_path` via the real `Sidecar` schema."""
    analysis = Analysis(
        title=title,
        description=description,
        ocr_text=ocr_text,
        tags=tags if tags is not None else ["excel", "office"],
        subjects=[],
        meme_type=meme_type,
        template=template,
        tone=tone if tone is not None else ["funny"],
        topics=topics if topics is not None else ["office"],
        language="en",
        nsfw=nsfw,
        confidence=confidence,
    )
    category = None
    if category_name is not None:
        category = CategoryAssignment(
            id=category_id,
            name=category_name,
            confidence=0.9,
            assigned_at="2026-09-17T00:00:00Z",
            model="test-model",
            run_id="test00000001",
        )
    sidecar = Sidecar(
        id="0123456789ab",
        file=FileInfo(name=media_path.name, width=100, height=100, bytes=1000, format="jpeg"),
        source=SourceInfo(
            original_filename=media_path.name,
            original_path=str(media_path),
            sha256="0" * 64,
            format="jpeg",
            width=100,
            height=100,
            bytes=1000,
            converted=False,
        ),
        analysis=analysis,
        category=category,
        processing=ProcessingInfo(
            tool_version="test",
            profile="fast",
            vision_model="test-model",
            prompt_version=1,
            processed_at="2026-09-17T00:00:00Z",
            duration_s=1.0,
            image_sent=ImageSent(width=100, height=100),
        ),
    )
    path = media_path.with_suffix(".json")
    sidecar.write(path)
    return path


@pytest.fixture
def library(tmp_path: Path) -> Path:
    lib = tmp_path / "library"
    (lib / "04-office-software-humor" / "image").mkdir(parents=True)
    (lib / "02-relationship-drama-comics" / "gif").mkdir(parents=True)
    (lib / "00 - NSFW" / "image").mkdir(parents=True)
    return lib


@pytest.fixture
def web_config(tmp_path: Path, library: Path) -> WebConfig:
    return WebConfig(
        library=library,
        data_dir=tmp_path / "webdata",
        inbox_dir=tmp_path / "inbox",
        scan_on_start=False,
        scan_interval_min=0,
        open_browser=False,
    )


@pytest.fixture
def make_indexer(web_config: WebConfig):
    """Factory for a standalone `Indexer` (its own `Database`) sharing `web_config`."""

    def _make(config: WebConfig | None = None) -> Indexer:
        cfg = config or web_config
        db = Database(cfg.resolved_data_dir() / "index.db")
        return Indexer(db, cfg.library, cfg)

    return _make


@pytest.fixture
def app_and_client(web_config: WebConfig):
    fastapi_app = create_app(web_config, dist_dir=web_config.data_dir / "no-such-dist")
    # The CSRF guard requires this header on every non-GET request; sending it by default
    # here keeps every existing test's POST/PUT/DELETE calls working. Tests that specifically
    # exercise the CSRF guard build their own bare `TestClient(fastapi_app)` instead.
    with TestClient(fastapi_app, headers={"X-Atomik-Meme": "1"}) as client:
        yield fastapi_app, client


@pytest.fixture
def client(app_and_client):
    return app_and_client[1]


@pytest.fixture
def fastapi_app(app_and_client):
    return app_and_client[0]
