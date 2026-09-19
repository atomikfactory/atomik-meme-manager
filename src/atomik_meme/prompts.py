"""Prompt texts and prompt_version constants for the vision and text models."""

from __future__ import annotations

import json
from typing import Any

PROMPT_VERSION = 3  # bumped for the gif/video frame-sampling prompts

# --- Vision analysis (per-image) ---

VISION_SYSTEM = (
    "You are a meticulous meme archivist. You describe internet memes for a searchable "
    "catalogue. Be concrete and literal; name recognisable people, characters, animals, "
    "games, apps, and meme templates when you are confident. Transcribe visible text "
    "exactly. Never invent text that is not visible.\n"
    "Only set `template` to a widely known, named meme template (for example "
    "'Distracted Boyfriend', 'Drake Hotline Bling', 'Two Buttons', 'Woman Yelling at a "
    "Cat'). If the image is not a recognised template, set `template` to null - never "
    "invent or guess a template name.\n"
    "Tags: lowercase, 1-2 words each, letters/digits/spaces only (no underscores), "
    "specific to this image. Never use generic tags such as 'meme', 'funny', 'image', "
    "'picture', or 'humor'.\n"
    "`language` is the ISO 639-1 code of the visible text (e.g. 'en', 'de'), 'mixed' if "
    "more than one language is visible, or 'none' if there is no visible text.\n"
    "Output must match the JSON schema exactly."
)


def vision_user_prompt(tiles: int) -> str:
    if tiles > 1:
        prefix = (
            f"Analyse this meme image. It is one tall image split into {tiles} sequential "
            "strips, top to bottom, with a small overlap between consecutive strips - treat "
            "them as one continuous image. "
        )
    else:
        prefix = "Analyse this meme image. "
    body = (
        "Produce: a short concrete title (3-8 words, no quotes, suitable as a filename), "
        "a one- or two-sentence description of what is happening and why it is funny, the "
        "exact visible text (ocr_text, empty string if none), 3-10 search tags, the main "
        "visual subjects, the meme_type, the meme template name if recognisable (null "
        "otherwise), 1-4 tone adjectives, 1-3 broad topics, the language of the text, "
        "whether it is NSFW, and your confidence."
    )
    return prefix + body


# --- OCR-only pass ---
#
# Measured on qwen3.5:4b: ocr_text embedded in the combined analysis call came back EMPTY on
# ~2 of 3 runs for tall/text-heavy images regardless of prompt wording, while a dedicated
# OCR-only call succeeded 6/6 with longer, more consistent transcripts (~1-2.5 s each). See
# `processing.ocr_pass` in config.py for the off/fallback/always modes built on this.

OCR_SYSTEM = "You are a precise OCR engine."


def ocr_user_prompt(tiles: int) -> str:
    prefix = (
        f"This is one tall image split into {tiles} sequential strips, top to bottom. "
        if tiles > 1
        else ""
    )
    return (
        f"{prefix}Transcribe ALL text visible in this image exactly as written, line by line "
        "in reading order, keeping the original language. Return an empty string only if "
        "there is no text."
    )


# --- Vision analysis for sampled gif/video frames ---
#
# Frames are chronological samples of ONE meme over time (not sequential strips of one still
# image, unlike the tall-image tiling prompts above) - the model must describe the meme as a
# whole, not narrate each frame.


def frames_vision_user_prompt(n_frames: int, duration_s: float, media_kind: str) -> str:
    kind_word = "video" if media_kind == "video" else "animated GIF"
    prefix = (
        f"These are {n_frames} frames sampled in chronological order from a {kind_word} "
        f"lasting {duration_s:.1f} s. Describe the meme as a whole - what happens over time "
        "and why it is funny - not the frames individually. "
    )
    return prefix + (
        "Produce: a short concrete title (3-8 words, no quotes, suitable as a filename), "
        "a one- or two-sentence description of what is happening and why it is funny, the "
        "exact visible text (ocr_text, empty string if none), 3-10 search tags, the main "
        "visual subjects, the meme_type, the meme template name if recognisable (null "
        "otherwise), 1-4 tone adjectives, 1-3 broad topics, the language of the text, "
        "whether it is NSFW, and your confidence."
    )


def frames_ocr_user_prompt(n_frames: int) -> str:
    return (
        f"These are {n_frames} frames sampled in chronological order. Transcribe all text "
        "visible across these frames in order; do not repeat text that persists across "
        "frames. Return an empty string only if there is no text."
    )


def vision_ocr_hint(ocr_text: str) -> str:
    """Appended to the analysis user prompt in `ocr_pass: always` mode."""
    return (
        "\n\nA separate OCR pass read the following text in the image (it may contain small "
        f"errors); use it to fill ocr_text and to understand the meme:\n{ocr_text}"
    )


# --- Categorisation: discovery (invent categories) ---

DISCOVERY_SYSTEM = (
    "You are organising a large collection of memes into a fixed set of browsable "
    "categories based on their themes, topics, tone, and meme type. You are never shown "
    "filenames - do not reference them. Categories must be distinct from one another, "
    "cover the bulk of the collection, and avoid heavy overlap. Avoid a category that "
    "would hold fewer than about 3% of the collection unless the collection clearly "
    "clusters that way. Do not invent an 'other'/'misc' category - that is added "
    "separately. Output must match the JSON schema exactly."
)


def discovery_user_prompt(
    stats: dict[str, Any],
    sample: list[dict[str, Any]],
    n_categories: int,
    pinned_names: list[str] | None = None,
) -> str:
    pinned_note = ""
    if pinned_names:
        names = ", ".join(sorted(pinned_names))
        pinned_note = (
            f"\nThese categories already exist (pinned, created from user hints or the NSFW "
            f"rule) - do not duplicate them: {names}.\n"
        )
    return (
        f"Here are statistics for a collection of {stats.get('total', len(sample))} memes:\n"
        f"{json.dumps(stats, ensure_ascii=False, indent=2)}\n\n"
        f"Here is a representative sample of {len(sample)} memes (id, title, tags, meme_type, "
        f"tone, topics, and a short OCR excerpt):\n"
        f"{json.dumps(sample, ensure_ascii=False, indent=2)}\n"
        f"{pinned_note}\n"
        f"Invent exactly {n_categories} distinct categories that best organise this whole "
        "collection, based on themes/topics/tone/meme type - not on filenames. For each "
        "category give: name (1-3 words, lowercase), description (one sentence), and a "
        "short list of keywords."
    )


# --- Categorisation: assignment (batch) ---

ASSIGN_SYSTEM = (
    "You are assigning individual memes to one of a fixed list of categories. Use only "
    "the provided category id, name, description, and keywords to decide. Every meme "
    "must receive exactly one category_id from the list and a confidence between 0 and "
    "1; if uncertain, still choose the closest category and lower your confidence rather "
    "than leaving it blank. The ids you are given are opaque identifiers, not filenames - "
    "copy each one back exactly, never invent or alter an id. Output must match the JSON "
    "schema exactly."
)


def assign_user_prompt(categories: list[dict[str, Any]], batch: list[dict[str, Any]]) -> str:
    return (
        "Categories (id, name, description, keywords):\n"
        f"{json.dumps(categories, ensure_ascii=False, indent=2)}\n\n"
        "Memes to assign (id, title, tags, meme_type, tone, topics, and a short OCR "
        "excerpt):\n"
        f"{json.dumps(batch, ensure_ascii=False, indent=2)}\n\n"
        "For every meme in the list above, output one assignment: its id (copied "
        "exactly), the category_id it best fits, your confidence (0-1), and a reason "
        "of at most 120 characters."
    )
