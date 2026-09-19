"""Shared test fixtures: synthetic images, a fake Ollama backend, and settings helpers."""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path

import pytest
from PIL import Image
from rich.console import Console

from atomik_meme.config import Settings
from atomik_meme.ollama_client import OllamaError

# --- Synthetic image builders -------------------------------------------------


def make_jpeg(path: Path, size=(300, 200), color=(30, 60, 200)) -> Path:
    Image.new("RGB", size, color).save(path, format="JPEG", quality=90)
    return path


def make_rgba_png(path: Path, size=(220, 160), color=(255, 0, 0, 128)) -> Path:
    Image.new("RGBA", size, color).save(path, format="PNG")
    return path


def make_webp(path: Path, size=(220, 160), color=(0, 200, 60)) -> Path:
    Image.new("RGB", size, color).save(path, format="WEBP")
    return path


def make_animated_gif(path: Path, size=(120, 100), frames: int = 3) -> Path:
    imgs = [Image.new("RGB", size, ((i * 60) % 256, 20, 20)) for i in range(frames)]
    imgs[0].save(path, format="GIF", save_all=True, append_images=imgs[1:], duration=80, loop=0)
    return path


def make_tall_comic(path: Path, size=(400, 4000)) -> Path:
    img = Image.new("RGB", size, (15, 15, 15))
    Image.new("RGB", size, (15, 15, 15))
    # a few horizontal bands so it's not a flat colour
    for i, band_color in enumerate([(200, 0, 0), (0, 200, 0), (0, 0, 200), (200, 200, 0)]):
        top = i * size[1] // 4
        bottom = (i + 1) * size[1] // 4
        for y in range(top, bottom):
            for x in range(0, size[0], 40):
                img.putpixel((x, y), band_color)
    img.save(path, format="JPEG", quality=90)
    return path


def make_corrupted_jpg(path: Path) -> Path:
    path.write_bytes(b"not a real image file" * 20)
    return path


def make_static_gif(path: Path, size=(120, 100), color=(80, 120, 200)) -> Path:
    """A single-frame (non-animated) GIF - still `media_type: gif`."""
    Image.new("RGB", size, color).save(path, format="GIF")
    return path


def make_animated_webp(path: Path, size=(120, 100), frames: int = 6) -> Path:
    imgs = [
        Image.new("RGB", size, ((i * 40) % 256, (255 - i * 30) % 256, 30)) for i in range(frames)
    ]
    imgs[0].save(
        path,
        format="WEBP",
        save_all=True,
        append_images=imgs[1:],
        duration=80,
        loop=0,
        minimize_size=True,
    )
    return path


def make_static_webp(path: Path, size=(120, 100), color=(0, 200, 60)) -> Path:
    Image.new("RGB", size, color).save(path, format="WEBP")
    return path


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def make_mp4(path: Path, size=(160, 120), duration_s: float = 2.0, fps: int = 10) -> Path:
    """A short synthetic mp4 built from Pillow frames piped into ffmpeg (skip if unavailable)."""
    import subprocess

    n_frames = max(1, round(duration_s * fps))
    frame_dir = path.parent / f"_{path.stem}_frames"
    frame_dir.mkdir(exist_ok=True)
    for i in range(n_frames):
        color = ((i * 25) % 256, (255 - i * 20) % 256, 40)
        Image.new("RGB", size, color).save(frame_dir / f"f{i:04d}.png")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-framerate",
            str(fps),
            "-i",
            str(frame_dir / "f%04d.png"),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )
    return path


# --- Fake Ollama chat backend --------------------------------------------------


def default_analysis(
    title: str = "test meme", confidence: float = 0.9, ocr_text: str = "TEST TEXT"
) -> dict:
    return {
        "title": title,
        "description": "A test meme used for unit testing purposes.",
        "ocr_text": ocr_text,
        "tags": ["testing", "unit test"],
        "subjects": ["test subject"],
        "meme_type": "reaction",
        "template": None,
        "tone": ["funny"],
        "topics": ["testing"],
        "language": "en",
        "nsfw": False,
        "confidence": confidence,
    }


def default_discovery(n: int = 9) -> dict:
    return {
        "categories": [
            {
                "name": f"category {i}",
                "description": f"Synthetic test category number {i}.",
                "keywords": [f"kw{i}a", f"kw{i}b"],
            }
            for i in range(1, n + 1)
        ]
    }


def _extract_json_block(content: str, marker: str):
    idx = content.index(marker) + len(marker)
    rest = content[idx:]
    if rest.startswith("\n"):
        rest = rest[1:]
    end = rest.index("\n\n")
    return json.loads(rest[:end])


class FakeOllama:
    """A fake `ChatBackend` (and preflight surface) that never touches the network."""

    def __init__(self) -> None:
        self.host = "fake://ollama"
        self.calls = 0
        self.analysis_queue: list = []
        self.discovery_queue: list = []
        self.assignment_handler = None
        self.fail_on_calls: set[int] = set()
        self.installed: set[str] = {"qwen3.5:4b", "qwen3.5:9b"}
        self.auto_pull_calls: list[str] = []
        self._auto_counter = 0
        self.calls_log: list[dict] = []
        self.ocr_text = "OCR TRANSCRIPT"  # canned reply for the dedicated OCR-only schema
        self.ocr_calls = 0
        self.ocr_error: Exception | None = None  # if set, the OCR-only call raises this once

    # --- preflight surface ---
    def version(self) -> str:
        return "0.32.5-fake"

    def list_models(self) -> list[str]:
        return sorted(self.installed)

    def has_model(self, name: str) -> bool:
        return name in self.installed or f"{name}:latest" in self.installed

    def pull(self, name: str, progress_cb=None) -> None:
        self.auto_pull_calls.append(name)
        self.installed.add(name)

    def close(self) -> None:
        pass

    def __call__(self, **kwargs):  # allows patching `OllamaClient` itself to this instance
        return self

    # --- chat ---
    def chat_structured(
        self,
        model: str,
        messages: list[dict],
        schema: dict,
        options: dict | None = None,
        think: bool | None = None,
        timeout_override: float | None = None,
    ) -> dict:
        self.calls += 1
        n = self.calls
        # Dispatch on the property set: the outgoing schema deliberately carries no
        # `title` keyword (see schema.GRAMMAR_UNSAFE_KEYWORDS).
        props = set((schema.get("properties") or {}).keys())
        if "meme_type" in props:
            title = "Analysis"
        elif "categories" in props:
            title = "DiscoveryOutput"
        elif "assignments" in props:
            title = "AssignmentOutput"
        elif props == {"ocr_text"}:
            title = "OcrOnly"
        else:
            title = ""
        self.calls_log.append(
            {
                "n": n,
                "title": title,
                "model": model,
                "messages": messages,
                "timeout_override": timeout_override,
            }
        )
        if n in self.fail_on_calls:
            raise OllamaError(f"simulated failure on call {n}")

        if title == "Analysis":
            if self.analysis_queue:
                item = self.analysis_queue.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
            self._auto_counter += 1
            return default_analysis(f"auto meme {self._auto_counter}")

        if title == "OcrOnly":
            self.ocr_calls += 1
            if self.ocr_error is not None:
                error, self.ocr_error = self.ocr_error, None
                raise error
            return {"ocr_text": self.ocr_text}

        if title == "DiscoveryOutput":
            if self.discovery_queue:
                item = self.discovery_queue.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
            # Honour whatever count categorize.category_count actually asked for (the schema
            # is built dynamically - see schema.build_discovery_output_model) rather than
            # hardcoding 9, so tests can exercise a non-default category_count end to end.
            n_required = (schema.get("properties", {}).get("categories", {})).get("minItems", 9)
            return default_discovery(n_required)

        if title == "AssignmentOutput":
            content = messages[-1]["content"]
            categories = _extract_json_block(
                content, "Categories (id, name, description, keywords):"
            )
            batch_marker = (
                "Memes to assign (id, title, tags, meme_type, tone, topics, "
                "and a short OCR excerpt):"
            )
            batch = _extract_json_block(content, batch_marker)
            if self.assignment_handler:
                assignments = self.assignment_handler(categories, batch)
            else:
                assignments = [
                    {
                        "id": r["id"],
                        "category_id": categories[0]["id"],
                        "confidence": 0.9,
                        "reason": "auto",
                    }
                    for r in batch
                ]
            return {"assignments": assignments}

        raise AssertionError(f"FakeOllama: unexpected schema title {title!r}")


@pytest.fixture(autouse=True)
def isolate_environment(tmp_path: Path, monkeypatch):
    """Never let a real OLLAMA_HOST, %APPDATA%/atomik-meme config, or atomik-meme-web library state
    file (%LOCALAPPDATA%/$XDG_STATE_HOME) leak into or out of tests."""
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata-unused"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata-unused"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state-unused"))


@pytest.fixture
def fake_ollama() -> FakeOllama:
    return FakeOllama()


@pytest.fixture
def patch_ollama(monkeypatch):
    """Return a function making processor.py/categorizer.py use `fake` instead of a real client."""

    def _patch(fake: FakeOllama) -> None:
        monkeypatch.setattr("atomik_meme.processor.OllamaClient", lambda **kwargs: fake)
        monkeypatch.setattr("atomik_meme.categorizer.OllamaClient", lambda **kwargs: fake)

    return _patch


@pytest.fixture
def settings() -> Settings:
    built = Settings()
    # Tests that exercise resume/force/duplicates need the sources to survive; the real
    # default (remove_from_input=True) is covered by test_consume_is_on_by_default.
    built.processing.remove_from_input = False
    return built


@pytest.fixture
def quiet_console() -> Console:
    return Console(file=io.StringIO(), width=120)


@pytest.fixture
def sample_input_dir(tmp_path: Path) -> Path:
    d = tmp_path / "input"
    d.mkdir()
    make_jpeg(d / "alpha.jpg")
    make_rgba_png(d / "bravo.png")
    make_webp(d / "charlie.webp")
    make_animated_gif(d / "delta.gif")
    make_tall_comic(d / "echo_tall.jpg")
    make_corrupted_jpg(d / "foxtrot_broken.jpg")
    shutil.copyfile(d / "alpha.jpg", d / "golf_dup.jpg")  # byte-identical duplicate
    return d
