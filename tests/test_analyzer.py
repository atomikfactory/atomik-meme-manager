"""Regression tests for the schema sent to Ollama, length pre-truncation, and the OCR pass."""

from __future__ import annotations

from atomik_meme.analyzer import (
    ANALYSIS_MAX_LENGTHS,
    ANALYSIS_SCHEMA,
    analyze_image,
    pretruncate_raw,
)
from atomik_meme.config import Settings
from atomik_meme.schema import GRAMMAR_UNSAFE_KEYWORDS, Analysis, to_ollama_schema
from tests.conftest import default_analysis


def _assert_no_unsafe_keywords(node, *, in_properties: bool = False) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if not in_properties:
                assert key not in GRAMMAR_UNSAFE_KEYWORDS, f"unsafe keyword {key!r} left in schema"
            _assert_no_unsafe_keywords(
                value, in_properties=(key == "properties" and not in_properties)
            )
    elif isinstance(node, list):
        for item in node:
            _assert_no_unsafe_keywords(item)


def test_analysis_schema_has_no_grammar_unsafe_keywords() -> None:
    # Observed live: `maxLength: 2000` on ocr_text made Ollama return HTTP 400
    # "Failed to initialize samplers: failed to parse grammar".
    _assert_no_unsafe_keywords(ANALYSIS_SCHEMA)
    assert "maxLength" not in ANALYSIS_SCHEMA["properties"]["ocr_text"]


def test_strip_keeps_property_names_that_collide_with_keywords() -> None:
    schema = to_ollama_schema(Analysis)
    # "title" is both a JSON-schema keyword and one of our property names.
    assert "title" in schema["properties"]
    assert schema["properties"]["title"]["type"] == "string"
    assert set(schema["required"]) >= {"title", "description", "tags"}
    # Constraints that the grammar converter handles fine are kept.
    assert schema["properties"]["tags"]["maxItems"] == 10
    assert schema["properties"]["meme_type"]["enum"]


def test_pretruncate_clips_to_model_limits() -> None:
    assert ANALYSIS_MAX_LENGTHS["ocr_text"] == 2000
    raw = {
        "title": "t" * 300,
        "description": "d" * 900,
        "ocr_text": "x" * 5000,
        "tags": ["a"] * 25,
        "subjects": ["s"] * 25,
        "meme_type": "reaction",
        "template": "y" * 200,
        "tone": ["funny"] * 9,
        "topics": ["work"] * 9,
        "language": "l" * 40,
        "nsfw": False,
        "confidence": 0.5,
    }
    clipped = pretruncate_raw(raw)
    assert len(clipped["ocr_text"]) == 2000
    assert len(clipped["title"]) == 120
    assert len(clipped["tags"]) == 10
    assert len(clipped["tone"]) == 4
    assert len(clipped["topics"]) == 3
    assert len(clipped["template"]) == 80
    # After clipping, validation succeeds without a repair round-trip.
    Analysis.model_validate(clipped)


# --- OCR pass (config.processing.ocr_pass: off | fallback | always) ---


def _vision_model_cfg():
    return Settings().vision_model("fast")


def test_ocr_off_makes_exactly_one_call(fake_ollama):
    fake_ollama.analysis_queue = [default_analysis("Title", ocr_text="")]
    analysis, _attempts, ocr_ran = analyze_image(
        fake_ollama, _vision_model_cfg(), ["deadbeef"], ocr_pass="off"
    )
    assert ocr_ran is False
    assert fake_ollama.calls == 1
    assert fake_ollama.ocr_calls == 0
    assert analysis.ocr_text == ""


def test_ocr_fallback_skips_when_analysis_already_has_text(fake_ollama):
    fake_ollama.analysis_queue = [default_analysis("Title", ocr_text="already here")]
    analysis, _attempts, ocr_ran = analyze_image(
        fake_ollama, _vision_model_cfg(), ["deadbeef"], ocr_pass="fallback"
    )
    assert ocr_ran is False
    assert fake_ollama.ocr_calls == 0
    assert analysis.ocr_text == "already here"


def test_ocr_fallback_fires_and_fills_in_when_analysis_ocr_text_is_empty(fake_ollama):
    fake_ollama.analysis_queue = [default_analysis("Title", ocr_text="")]
    fake_ollama.ocr_text = "RECOVERED TEXT"
    analysis, _attempts, ocr_ran = analyze_image(
        fake_ollama, _vision_model_cfg(), ["deadbeef"], ocr_pass="fallback"
    )
    assert ocr_ran is True
    assert fake_ollama.ocr_calls == 1
    assert analysis.ocr_text == "RECOVERED TEXT"


def test_ocr_always_runs_first_and_injects_hint_into_analysis_prompt(fake_ollama):
    fake_ollama.ocr_text = "HINT TEXT HERE"
    fake_ollama.analysis_queue = [default_analysis("Title", ocr_text="analysis's own text")]

    analysis, _attempts, ocr_ran = analyze_image(
        fake_ollama, _vision_model_cfg(), ["deadbeef"], ocr_pass="always"
    )
    assert ocr_ran is True
    assert fake_ollama.ocr_calls == 1
    # OCR ran before the analysis call, and its text was handed to the analysis call as a hint.
    assert fake_ollama.calls_log[0]["title"] == "OcrOnly"
    analysis_call = next(c for c in fake_ollama.calls_log if c["title"] == "Analysis")
    user_content = analysis_call["messages"][-1]["content"]
    assert "HINT TEXT HERE" in user_content
    # The dedicated OCR transcript wins over the analysis call's own (unreliable) ocr_text.
    assert analysis.ocr_text == "HINT TEXT HERE"


def test_ocr_always_degrades_gracefully_when_ocr_call_itself_fails(fake_ollama):
    from atomik_meme.ollama_client import OllamaError

    fake_ollama.ocr_error = OllamaError("ocr broke")
    fake_ollama.analysis_queue = [default_analysis("Title")]
    analysis, _attempts, ocr_ran = analyze_image(
        fake_ollama, _vision_model_cfg(), ["deadbeef"], ocr_pass="always"
    )
    # the OCR call failing must not abort the image - it just proceeds without a hint
    assert ocr_ran is True
    assert analysis.title == "Title"


def test_clip_to_limits_handles_nested_assignment_output() -> None:
    from atomik_meme.schema import build_assignment_output_model, clip_to_limits

    model = build_assignment_output_model(10)
    raw = {
        "assignments": [
            {"id": "a" * 16, "category_id": 3, "confidence": 0.9, "reason": "r" * 400},
            {"id": "b" * 16, "category_id": 10, "confidence": 0.2, "reason": "short"},
        ]
    }
    clipped = clip_to_limits(raw, model)
    assert len(clipped["assignments"][0]["reason"]) == 120
    assert clipped["assignments"][1]["reason"] == "short"
    model.model_validate(clipped)  # would raise string_too_long without clipping


def test_clip_to_limits_handles_discovery_output() -> None:
    from atomik_meme.schema import build_discovery_output_model, clip_to_limits

    model = build_discovery_output_model(2)
    raw = {
        "categories": [
            {"name": "n" * 100, "description": "d" * 900, "keywords": ["k"] * 30},
            {"name": "ok", "description": "fine", "keywords": []},
        ]
    }
    clipped = clip_to_limits(raw, model)
    assert len(clipped["categories"][0]["name"]) == 40
    assert len(clipped["categories"][0]["description"]) == 200
    assert len(clipped["categories"][0]["keywords"]) == 10
    model.model_validate(clipped)


def test_ocr_always_keeps_analysis_text_when_ocr_call_returns_nothing(fake_ollama):
    fake_ollama.ocr_text = ""
    fake_ollama.analysis_queue = [default_analysis("Title", ocr_text="analysis's own text")]
    analysis, _attempts, ocr_ran = analyze_image(
        fake_ollama, _vision_model_cfg(), ["deadbeef"], ocr_pass="always"
    )
    assert ocr_ran is True
    assert analysis.ocr_text == "analysis's own text"
