"""Vision call -> Analysis: validation, one repair attempt, post-normalisation."""

from __future__ import annotations

import json
import logging
import re

from pydantic import ValidationError

from atomik_meme.config import DEFAULT_VISION_NUM_PREDICT
from atomik_meme.ollama_client import ChatBackend, OllamaError
from atomik_meme.prompts import (
    OCR_SYSTEM,
    VISION_SYSTEM,
    frames_ocr_user_prompt,
    frames_vision_user_prompt,
    ocr_user_prompt,
    vision_ocr_hint,
    vision_user_prompt,
)
from atomik_meme.schema import Analysis, clip_to_limits, field_max_length, to_ollama_schema

logger = logging.getLogger(__name__)

ANALYSIS_SCHEMA = to_ollama_schema(Analysis)

# Plain dict, deliberately with none of GRAMMAR_UNSAFE_KEYWORDS - it doesn't need them (a single
# unconstrained string), and this is the one schema not derived from a pydantic model.
OCR_SCHEMA: dict = {
    "type": "object",
    "properties": {"ocr_text": {"type": "string"}},
    "required": ["ocr_text"],
}


ANALYSIS_MAX_LENGTHS = {
    name: field_max_length(field)
    for name, field in Analysis.model_fields.items()
    if field_max_length(field) is not None
}


def pretruncate_raw(raw: dict) -> dict:
    """Clip over-long strings/lists to the Analysis schema's limits (see schema.clip_to_limits)."""
    return clip_to_limits(raw, Analysis)


GENERIC_TAGS = {"meme", "memes", "funny", "image", "picture", "pictures", "humor", "humour"}

_WHITESPACE_RE = re.compile(r"\s+")


class AnalysisError(Exception):
    """Raised when the model's output still fails validation after the repair attempt."""

    def __init__(self, message: str, attempts: int) -> None:
        super().__init__(message)
        self.attempts = attempts


def _clean_word_list(items: list, max_len: int, drop_generic: bool = False) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in items:
        if not isinstance(raw, str):
            continue
        cleaned = raw.replace("_", " ").replace("-", " ").strip().lower()
        cleaned = _WHITESPACE_RE.sub(" ", cleaned)
        if not cleaned:
            continue
        if drop_generic and cleaned in GENERIC_TAGS:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
        if len(result) >= max_len:
            break
    return result


def normalize_analysis(analysis: Analysis) -> Analysis:
    """Lowercase/dedupe/cap lists, clamp confidence, strip strings, template '' -> None."""
    data = analysis.model_dump()

    tags = _clean_word_list(data.get("tags") or [], max_len=10, drop_generic=True)
    data["tags"] = tags or ["uncategorized"]

    tone = _clean_word_list(data.get("tone") or [], max_len=4)
    data["tone"] = tone or ["neutral"]

    topics = _clean_word_list(data.get("topics") or [], max_len=3)
    data["topics"] = topics or ["general"]

    data["subjects"] = _clean_word_list(data.get("subjects") or [], max_len=10)

    template = data.get("template")
    if isinstance(template, str):
        template = template.strip()
        data["template"] = template or None

    title = _WHITESPACE_RE.sub(" ", (data.get("title") or "").replace("_", " ")).strip()
    data["title"] = title or "untitled meme"
    data["description"] = (data.get("description") or "").strip()
    data["ocr_text"] = (data.get("ocr_text") or "").strip()

    language = (data.get("language") or "none").strip().lower()
    data["language"] = language or "none"

    try:
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    data["confidence"] = max(0.0, min(1.0, confidence))

    return Analysis.model_validate(data)


def _call_ocr(
    client: ChatBackend,
    model_cfg,
    tiles_b64: list[str],
    timeout_override: float | None,
    media_kind: str = "image",
) -> str:
    """Run the OCR-only call and return the (pretruncated) transcript, or '' on any failure."""
    prompt = (
        ocr_user_prompt(len(tiles_b64))
        if media_kind == "image"
        else frames_ocr_user_prompt(len(tiles_b64))
    )
    messages = [
        {"role": "system", "content": OCR_SYSTEM},
        {"role": "user", "content": prompt, "images": tiles_b64},
    ]
    options = {
        "num_ctx": model_cfg.num_ctx,
        "temperature": model_cfg.temperature,
        "num_predict": model_cfg.num_predict or DEFAULT_VISION_NUM_PREDICT,
    }
    try:
        raw = client.chat_structured(
            model=model_cfg.name,
            messages=messages,
            schema=OCR_SCHEMA,
            options=options,
            think=model_cfg.think,
            timeout_override=timeout_override,
        )
    except OllamaError as exc:
        logger.warning("OCR pass failed, continuing without it: %s", exc)
        return ""
    text = raw.get("ocr_text", "") if isinstance(raw, dict) else ""
    if not isinstance(text, str):
        text = str(text)
    limit = ANALYSIS_MAX_LENGTHS.get("ocr_text", 2000)
    return text[:limit].rstrip()


def analyze_image(
    client: ChatBackend,
    model_cfg,
    tiles_b64: list[str],
    timeout_override: float | None = None,
    ocr_pass: str = "fallback",
    media_kind: str = "image",
    duration_s: float | None = None,
) -> tuple[Analysis, int, bool]:
    """Call the vision model and return a normalised `Analysis`, the attempt count, and
    whether a dedicated OCR-only call ran (see `processing.ocr_pass` in config.py):

    `media_kind` selects the prompt: "image" uses the single/tall-image-tile prompts;
    "gif"/"video" use the frame-sampling prompts, which describe the
    meme as a whole across chronologically sampled frames rather than narrating each one;
    `duration_s` (required for a meaningful prompt in that case) is the gif/video's length.

    - "off": a single combined analysis call only.
    - "always": OCR runs first; if it returns text, it is handed to the analysis call as a
      hint, and that transcript replaces the analysis call's own `ocr_text` (which is
      empty ~2 of 3 times for text-heavy images on small models).
    - "fallback" (default): the analysis call runs first; OCR only runs afterwards, and only
      if the analysis came back with an empty `ocr_text`, in which case it replaces it.

    On a schema-validation failure for the analysis call, the raw response and the
    validation error are appended to the conversation and one repair attempt is made;
    a second failure raises `AnalysisError`.
    """
    ocr_ran = False
    ocr_hint = ""
    ocr_text = ""
    if ocr_pass == "always":
        ocr_ran = True
        ocr_text = _call_ocr(client, model_cfg, tiles_b64, timeout_override, media_kind)
        if ocr_text:
            ocr_hint = vision_ocr_hint(ocr_text)

    if media_kind == "image":
        user_prompt = vision_user_prompt(len(tiles_b64))
    else:
        user_prompt = frames_vision_user_prompt(len(tiles_b64), duration_s or 0.0, media_kind)

    messages: list[dict] = [
        {"role": "system", "content": VISION_SYSTEM},
        {
            "role": "user",
            "content": user_prompt + ocr_hint,
            "images": tiles_b64,
        },
    ]
    options = {"num_ctx": model_cfg.num_ctx, "temperature": model_cfg.temperature}
    options["num_predict"] = model_cfg.num_predict or DEFAULT_VISION_NUM_PREDICT

    attempts = 0
    last_error: Exception | None = None
    max_attempts = 2
    analysis: Analysis | None = None
    while attempts < max_attempts:
        attempts += 1
        raw = client.chat_structured(
            model=model_cfg.name,
            messages=messages,
            schema=ANALYSIS_SCHEMA,
            options=options,
            think=model_cfg.think,
            timeout_override=timeout_override,
        )
        try:
            analysis = Analysis.model_validate(pretruncate_raw(raw))
            break
        except ValidationError as exc:
            last_error = exc
            logger.info(
                "Analysis validation failed (attempt %d/%d): %s", attempts, max_attempts, exc
            )
            if attempts < max_attempts:
                messages.append(
                    {"role": "assistant", "content": json.dumps(raw, ensure_ascii=False)}
                )
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "That response did not match the required schema. Validation "
                            f"error:\n{exc}\nPlease answer again with corrected JSON that "
                            "matches the schema exactly."
                        ),
                    }
                )

    if analysis is None:
        raise AnalysisError(
            f"Validation failed after {attempts} attempt(s): {last_error}", attempts
        )

    if ocr_pass == "always" and ocr_text:
        # The dedicated OCR call is the reliable one (measured 6/6 vs ~1/3 for the
        # combined call); its transcript wins over the analysis call's own ocr_text.
        analysis = analysis.model_copy(
            update={"ocr_text": ocr_text[: ANALYSIS_MAX_LENGTHS["ocr_text"]]}
        )
    analysis = normalize_analysis(analysis)

    if ocr_pass == "fallback" and not analysis.ocr_text:
        ocr_ran = True
        # Deliberately not touching `language`: filling in text we didn't read ourselves is
        # fine, guessing its language from it is not.
        ocr_text = _call_ocr(client, model_cfg, tiles_b64, timeout_override, media_kind)
        if ocr_text:
            analysis = analysis.model_copy(update={"ocr_text": ocr_text})

    return analysis, attempts, ocr_ran
