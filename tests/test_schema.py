import json
from pathlib import Path

from atomik_meme.schema import (
    Analysis,
    AssignmentOutput,
    DiscoveryOutput,
    FileInfo,
    ProcessingInfo,
    Sidecar,
    SourceInfo,
    to_ollama_schema,
)


def _make_sidecar(title: str, ocr_text: str) -> Sidecar:
    return Sidecar(
        id="0123456789abcdef",
        file=FileInfo(name="test.jpg", width=10, height=10, bytes=100, format="jpeg"),
        source=SourceInfo(
            original_filename="test.png",
            original_path="C:/memes/test.png",
            sha256="0" * 64,
            format="png",
            width=10,
            height=10,
            bytes=90,
            converted=True,
        ),
        analysis=Analysis(
            title=title,
            description="A description.",
            ocr_text=ocr_text,
            tags=["a", "b"],
            meme_type="reaction",
            tone=["funny"],
            topics=["general"],
            confidence=0.5,
        ),
        processing=ProcessingInfo(
            tool_version="0.1.0",
            profile="fast",
            vision_model="qwen3.5:4b",
            prompt_version=1,
            processed_at="2026-09-16T00:00:00Z",
            duration_s=1.0,
            image_sent={"width": 10, "height": 10, "tiles": 1},
        ),
    )


def test_sidecar_round_trips_non_cp1252_characters(tmp_path: Path):
    """Turkish/German characters must survive write -> read without mangling (UTF-8 files)."""
    sidecar = _make_sidecar(
        title="Büyük İşler Başarmak", ocr_text="Işıklar söndü, ähnliches Problem"
    )
    path = tmp_path / "test.json"
    sidecar.write(path)

    # the file on disk must be valid UTF-8 (never cp1252/mbcs) and contain the raw characters
    raw = path.read_text(encoding="utf-8")
    assert "İ" in raw
    assert "ä" in raw

    reloaded = Sidecar.read(path)
    assert reloaded.analysis.title == sidecar.analysis.title
    assert reloaded.analysis.ocr_text == sidecar.analysis.ocr_text
    assert "İşler" in reloaded.analysis.title
    assert "ähnliches" in reloaded.analysis.ocr_text


def _assert_no_ref_or_defs(schema: dict) -> None:
    text = json.dumps(schema)
    assert "$ref" not in text
    assert "$defs" not in text


def test_analysis_schema_has_no_ref_or_defs():
    schema = to_ollama_schema(Analysis)
    _assert_no_ref_or_defs(schema)
    assert "meme_type" in schema["properties"]  # no `title` keyword is sent to Ollama
    # template must remain optional (string or null), just not via $ref
    assert "template" in schema["properties"]


def test_discovery_and_assignment_schemas_have_no_ref_or_defs():
    _assert_no_ref_or_defs(to_ollama_schema(DiscoveryOutput))
    _assert_no_ref_or_defs(to_ollama_schema(AssignmentOutput))
