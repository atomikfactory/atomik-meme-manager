"""Pydantic models for sidecar metadata, categories, index, and run summaries.

`Analysis` is the object the vision model must return; its JSON Schema (via
`to_ollama_schema`) is passed to Ollama's structured-output `format` parameter.
Keep `Analysis` free of nested BaseModels so the schema has no `$ref`/`$defs`
that some Ollama versions handle poorly for the *primary* extraction call.
`to_ollama_schema` still inlines `$defs` generically for the discovery/assign
schemas, which do nest models.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

SCHEMA_VERSION = 2

MediaType = Literal["image", "gif", "video"]

MemeType = Literal[
    "reaction",
    "image-macro",
    "comic",
    "screenshot",
    "text-post",
    "photo",
    "drawing",
    "chart",
    "template",
    "other",
]


def utcnow_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


def _inline_defs(node, defs: dict):
    if isinstance(node, dict):
        if set(node.keys()) == {"$ref"}:
            name = node["$ref"].rsplit("/", 1)[-1]
            target = defs.get(name, {})
            return _inline_defs(dict(target), defs)
        return {k: _inline_defs(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_inline_defs(v, defs) for v in node]
    return node


# Keywords that llama.cpp's JSON-schema-to-grammar converter either ignores or turns into a
# grammar it then fails to parse (observed: `maxLength: 2000` -> HTTP 400 "failed to parse
# grammar"). Length and pattern limits are enforced after the fact by pydantic instead.
GRAMMAR_UNSAFE_KEYWORDS: frozenset[str] = frozenset(
    {"minLength", "maxLength", "pattern", "format", "default", "title", "examples"}
)


def _strip_keywords(node: Any, keywords: frozenset[str], *, in_properties: bool = False) -> Any:
    """Recursively drop `keywords`, never touching property *names* under `properties`."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if not in_properties and key in keywords:
                continue
            out[key] = _strip_keywords(
                value, keywords, in_properties=(key == "properties" and not in_properties)
            )
        return out
    if isinstance(node, list):
        return [_strip_keywords(item, keywords) for item in node]
    return node


def field_max_length(field) -> int | None:
    for meta in field.metadata:
        limit = getattr(meta, "max_length", None)
        if isinstance(limit, int):
            return limit
    return None


def _model_in_annotation(annotation):
    """Return (item_model, is_list) if the annotation is a BaseModel or list[BaseModel]."""
    from typing import get_args, get_origin

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation, False
    if get_origin(annotation) is list:
        args = get_args(annotation)
        if args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
            return args[0], True
    return None, False


def clip_to_limits(raw: Any, model: type[BaseModel]) -> Any:
    """Clip over-long strings/lists in `raw` to `model`'s `max_length` constraints, recursively.

    Length keywords are deliberately not sent to Ollama (they break its grammar
    converter, see GRAMMAR_UNSAFE_KEYWORDS), so the model may exceed them; clipping
    before validation avoids failing - and burning a retry - purely on length.
    """
    if not isinstance(raw, dict):
        return raw
    out = dict(raw)
    for name, field in model.model_fields.items():
        if name not in out:
            continue
        value = out[name]
        limit = field_max_length(field)
        if isinstance(value, str) and limit is not None and len(value) > limit:
            value = value[:limit].rstrip()
        elif isinstance(value, list) and limit is not None and len(value) > limit:
            value = value[:limit]
        item_model, is_list = _model_in_annotation(field.annotation)
        if item_model is not None:
            if is_list and isinstance(value, list):
                value = [clip_to_limits(v, item_model) for v in value]
            elif not is_list:
                value = clip_to_limits(value, item_model)
        out[name] = value
    return out


def to_ollama_schema(model: type[BaseModel]) -> dict:
    """Return a plain JSON Schema (no $defs/$ref, no grammar-unsafe keywords) for `format`."""
    schema = model.model_json_schema()
    defs = schema.get("$defs", {})
    inlined = _inline_defs(schema, defs)
    return _strip_keywords(inlined, GRAMMAR_UNSAFE_KEYWORDS)


class Analysis(BaseModel):
    """The structured extraction produced by the vision model for one meme image."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(
        description="Short concrete title, 3-8 words, no quotes, suitable as a filename.",
        min_length=1,
        max_length=120,
    )
    description: str = Field(
        description="One or two sentences describing what is happening and why it is funny.",
        min_length=1,
        max_length=500,
    )
    ocr_text: str = Field(
        default="",
        description="Exact visible text transcribed verbatim; empty string if none.",
        max_length=2000,
    )
    tags: list[str] = Field(
        description="3-10 lowercase search tags.",
        min_length=1,
        max_length=10,
    )
    subjects: list[str] = Field(
        default_factory=list,
        description="Main visual subjects (people, objects, characters, animals).",
        max_length=10,
    )
    meme_type: MemeType = Field(description="One of the fixed meme-type categories.")
    template: str | None = Field(
        default=None,
        description="Recognised meme template name, or null if not a known template.",
        max_length=80,
    )
    tone: list[str] = Field(
        description="1-4 lowercase tone adjectives.",
        min_length=1,
        max_length=4,
    )
    topics: list[str] = Field(
        description="1-3 broad themes, used heavily by categorisation.",
        min_length=1,
        max_length=3,
    )
    language: str = Field(
        default="none",
        description="BCP-47-ish language code of the OCR text, or 'none' if no text.",
        max_length=20,
    )
    nsfw: bool = Field(default=False, description="Whether the image is not safe for work.")
    confidence: float = Field(
        description="Model's self-rated confidence in the description, 0-1.",
        ge=0.0,
        le=1.0,
    )


class FileInfo(BaseModel):
    name: str
    width: int
    height: int
    bytes: int
    format: str
    animated: bool = False
    # v2 additions; defaulted so v1 sidecars still load.
    media_type: MediaType = "image"
    duration_s: float | None = None
    fps: float | None = None
    frame_count: int | None = None
    has_audio: bool | None = None  # video only, else null
    over_duration_limit: bool = False


class SourceInfo(BaseModel):
    original_filename: str
    original_path: str
    sha256: str
    format: str
    width: int
    height: int
    bytes: int
    converted: bool


class CategoryAssignment(BaseModel):
    id: int
    name: str
    confidence: float
    assigned_at: str
    model: str
    run_id: str
    suggested: str | None = None
    # v2: how this assignment was decided (defaulted so v1 sidecars still load as "llm").
    source: Literal["llm", "hint", "rule"] = "llm"


class ImageSent(BaseModel):
    width: int
    height: int
    tiles: int = 1
    frames: int = (
        1  # gif/video: frames sampled; stays 1 for the image pipeline (tiles is used there)
    )


class ProcessingInfo(BaseModel):
    tool_version: str
    profile: str
    vision_model: str
    ollama_version: str = ""
    prompt_version: int
    processed_at: str
    duration_s: float
    image_sent: ImageSent
    attempts: int = 1
    ocr_pass: str | None = None  # "fallback" | "always" when a dedicated OCR call ran, else None


class Sidecar(BaseModel):
    schema_version: int = SCHEMA_VERSION
    id: str
    file: FileInfo
    source: SourceInfo
    analysis: Analysis
    category: CategoryAssignment | None = None
    processing: ProcessingInfo

    def write(self, path: Path) -> None:
        data = self.model_dump(mode="json")
        atomic_write_json(Path(path), data)

    @classmethod
    def read(cls, path: Path) -> Sidecar:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.model_validate(data)


class Category(BaseModel):
    id: int
    name: str
    folder: str
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    count: int = 0
    # v2: pinned categories (id 0 NSFW, ids 11+ user-created) are excluded from
    # discovery/assignment and never touched by --rediscover.
    pinned: bool = False
    rule: Literal["nsfw"] | None = None
    source: Literal["llm", "user", "rule"] = "llm"


class CategoriesFile(BaseModel):
    schema_version: int = SCHEMA_VERSION
    generated_at: str
    model: str
    profile: str
    run_id: str
    collection_size: int
    layout_version: int = 1  # v1 files loaded without this key default to 1; v2 writers set 2
    categories: list[Category]

    def write(self, path: Path) -> None:
        atomic_write_json(Path(path), self.model_dump(mode="json"))

    @classmethod
    def read(cls, path: Path) -> CategoriesFile:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.model_validate(data)


class IndexEntry(BaseModel):
    id: str
    sha256: str
    stem: str
    relpath: str
    status: Literal["done", "failed", "duplicate"] = "done"
    category_id: int | None = None
    processed_at: str = ""


class FailureRecord(BaseModel):
    source_path: str
    stage: str
    message: str


class CreatedCategory(BaseModel):
    """A pinned category (hint-created, or the NSFW rule) newly registered this run."""

    id: int
    name: str
    folder: str


class RunSummary(BaseModel):
    schema_version: int = SCHEMA_VERSION
    run_id: str
    command: str
    started_at: str
    finished_at: str
    duration_s: float
    profile: str = ""
    vision_model: str = ""
    text_model: str = ""
    processed: int = 0
    skipped_done: int = 0
    skipped_duplicate: int = 0
    failed: int = 0
    categorized: int = 0
    failures: list[FailureRecord] = Field(default_factory=list)
    output_path: str = ""
    # v2 additions
    hinted: int = 0  # files placed directly via --category/subfolder hint
    categories_created: list[CreatedCategory] = Field(
        default_factory=list
    )  # pinned categories newly registered this run (hint, or the NSFW rule) - never null
    consume: bool = False  # whether --consume was active this run
    removed_from_input: int = 0
    moved: int = 0  # `migrate`: pairs moved into the v2 <category>/<media>/ layout
    sidecars_upgraded: int = 0  # `migrate`: sidecars rewritten from schema_version < 2

    def write(self, path: Path) -> None:
        atomic_write_json(Path(path), self.model_dump(mode="json"))


# --- Categorisation LLM I/O schemas (nested models; $defs inlined for Ollama) ---
#
# These are built dynamically because `categorize.category_count` is a config
# value, not a constant: the number of discovered categories (category_count -
# 1) and the valid range for `category_id` (1..category_count) both depend on
# it. `build_*` factories are called once per `categorize` run with the
# effective count; the module-level `DiscoveryOutput`/`AssignmentOutput`/
# `AssignmentItem` names below are simply the factories applied to the
# documented default (category_count=10) for callers that just want "the
# normal schema" (e.g. tests, or code that never overrides the count).


class DiscoveryCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="1-3 words, lowercase, distinct category name.", max_length=40)
    description: str = Field(description="One sentence describing the category.", max_length=200)
    keywords: list[str] = Field(default_factory=list, max_length=10)


def build_discovery_output_model(n_categories: int) -> type[BaseModel]:
    """A `DiscoveryOutput`-shaped model requiring exactly `n_categories` categories."""
    return create_model(
        "DiscoveryOutput",
        __config__=ConfigDict(extra="forbid"),
        categories=(
            list[DiscoveryCategory],
            Field(min_length=n_categories, max_length=n_categories),
        ),
    )


def build_assignment_item_model(max_category_id: int) -> type[BaseModel]:
    """An `AssignmentItem`-shaped model whose `category_id` must be in `1..max_category_id`."""
    return create_model(
        "AssignmentItem",
        __config__=ConfigDict(extra="forbid"),
        id=(str, Field(description="The 16-hex id of the meme record, copied verbatim.")),
        category_id=(int, Field(ge=1, le=max_category_id)),
        confidence=(float, Field(ge=0.0, le=1.0)),
        reason=(str, Field(default="", max_length=120)),
    )


def build_assignment_output_model(max_category_id: int) -> type[BaseModel]:
    """An `AssignmentOutput`-shaped model built on `build_assignment_item_model`."""
    item_model = build_assignment_item_model(max_category_id)
    return create_model(
        "AssignmentOutput",
        __config__=ConfigDict(extra="forbid"),
        assignments=(list[item_model], ...),
    )


DiscoveryOutput = build_discovery_output_model(9)
AssignmentItem = build_assignment_item_model(10)
AssignmentOutput = build_assignment_output_model(10)
