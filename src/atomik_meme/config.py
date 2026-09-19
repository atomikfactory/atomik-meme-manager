"""Settings models, config file resolution/merging, env overrides, sample writer."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError

logger = logging.getLogger(__name__)

APP_NAME = "atomik-meme"


class SettingsError(Exception):
    """Raised when the config file is malformed (wrong types, bad YAML)."""


# --- The authoritative sample config (also written by `init-config`) ---

SAMPLE_YAML = """\
ollama:
  host: http://127.0.0.1:11434
  timeout_s: 180            # per request
  retries: 2                # on transport errors / 5xx / invalid JSON
  keep_alive: 10m
  auto_pull: false
  default_profile: fast

# num_predict caps generated tokens per request so a runaway repetition loop is cut off
# instead of running until num_ctx is exhausted.
models:
  vision:
    fast:     { name: qwen3.5:4b, num_ctx: 8192, temperature: 0.2, think: false, num_predict: 1200 }
    accurate: { name: qwen3.5:9b, num_ctx: 8192, temperature: 0.2, think: false, num_predict: 1200 }
  text:
    fast:     { name: qwen3.5:4b, num_ctx: 16384, temperature: 0.3, think: false,
                num_predict: 4096 }
    accurate: { name: qwen3.5:9b, num_ctx: 16384, temperature: 0.3, think: false,
                num_predict: 4096 }

processing:
  workers: 1
  recursive: true
  extensions: [.jpg, .jpeg, .png, .webp, .gif, .bmp, .tiff, .tif]
  shutdown_grace_s: 30
  # OCR reliability: on qwen3.5:4b, ocr_text embedded in the combined analysis call came back
  # EMPTY on ~2 of 3 runs for tall/text-heavy images regardless of prompt wording; a dedicated
  # OCR-only call succeeded 6/6 with longer, more consistent transcripts (~1-2.5 s each).
  #   off      - a single combined analysis call (fastest, least reliable OCR).
  #   fallback - (default) run the OCR-only call only when the analysis call returned an empty
  #              ocr_text: ~+0 s for memes already read correctly, ~+1-2 s otherwise.
  #   always   - run OCR first and feed its transcript into the analysis prompt as a hint (best
  #              quality; ~+2 s per image).
  ocr_pass: always
  # Extensions treated as `video`; everything else falls back to `image`/`gif`
  # detection (an animated .webp/.png is `gif`, a plain .gif is always `gif`).
  video_extensions: [.mp4, .webm, .mov, .mkv, .m4v, .avi]
  # Delete a source from INPUT once its output pair is written and verified (or, for a
  # skipped duplicate/already-done source, once the indexed pair is verified on disk).
  # Never removes on failure or --dry-run. Overridden per run by --consume/--no-consume.
  remove_from_input: true

# ffmpeg/ffprobe access for gif/video frame sampling. `check` reports whichever
# versions it finds (or "not found") on these paths.
media:
  ffmpeg_path: ffmpeg
  ffprobe_path: ffprobe
  frames_per_video: 6              # evenly spaced timestamps, incl. ~0 and ~end
  frames_per_gif: 6                # evenly spaced frames, incl. first and last
  max_video_duration_s: 180        # longer videos still process; sidecar gets over_duration_limit
  frame_max_total_pixels: 4500000  # combined budget across all sampled frames in one request

image:
  output_jpeg_quality: 92           # for converted files; JPEG sources are copied verbatim
  copy_jpeg_verbatim: true
  # what the model sees (never affects stored files):
  model_max_pixels: 1200000
  model_max_side: 1600
  model_jpeg_quality: 88
  tall_image:                       # multi-panel comics
    enabled: true
    min_aspect: 2.5                 # height/width (or width/height) above which we tile
    min_long_side: 1600
    max_tiles: 4
    overlap_px: 40
    max_total_pixels: 4500000       # sum of pixels across all tiles in one request (~3600 tokens)

naming:
  max_slug_length: 80
  drop_leading_articles: true

categorize:
  category_count: 10                # includes "others"
  others_name: others
  discovery_sample_size: 150        # memes shown to the LLM when inventing categories
  top_tags_in_digest: 60
  assign_batch_size: 12
  min_confidence: 0.5               # below -> others
  workers: 1
  max_retries: 3
  # Memes the vision model flagged nsfw are auto-routed to this pinned category (id 0),
  # verbatim folder name, unless an explicit hint names another category (hint wins).
  # Excluded from discovery/assignment either way. `enabled: false` disables the rule
  # entirely (nsfw memes are then treated like any other).
  nsfw:
    enabled: true
    name: nsfw
    folder: "00 - NSFW"

logging:
  level: INFO
  file: true                        # writes to OUTPUT/.meme-manager/logs/
"""


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow")


class OllamaConfig(_Base):
    host: str = "http://127.0.0.1:11434"
    timeout_s: float = 180
    retries: int = 2
    keep_alive: str = "10m"
    auto_pull: bool = False
    default_profile: str = "fast"


# Output-token caps per role. A small model can fall into a repetition loop inside a JSON
# string; without a cap it runs until num_ctx is exhausted (observed: 97 s for one image).
DEFAULT_VISION_NUM_PREDICT = 1200
DEFAULT_TEXT_NUM_PREDICT = 4096


class ModelSpec(_Base):
    name: str
    num_ctx: int = 8192
    temperature: float = 0.2
    think: bool | None = False
    num_predict: int | None = None  # None -> role default (see DEFAULT_*_NUM_PREDICT)


class VisionModels(_Base):
    fast: ModelSpec = Field(default_factory=lambda: ModelSpec(name="qwen3.5:4b", num_ctx=8192))
    accurate: ModelSpec = Field(default_factory=lambda: ModelSpec(name="qwen3.5:9b", num_ctx=8192))


class TextModels(_Base):
    fast: ModelSpec = Field(
        default_factory=lambda: ModelSpec(name="qwen3.5:4b", num_ctx=16384, temperature=0.3)
    )
    accurate: ModelSpec = Field(
        default_factory=lambda: ModelSpec(name="qwen3.5:9b", num_ctx=16384, temperature=0.3)
    )


class ModelsConfig(_Base):
    vision: VisionModels = Field(default_factory=VisionModels)
    text: TextModels = Field(default_factory=TextModels)


class ProcessingConfig(_Base):
    workers: int = 1
    recursive: bool = True
    extensions: list[str] = Field(
        default_factory=lambda: [".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif"]
    )
    shutdown_grace_s: float = 30
    ocr_pass: Literal["off", "fallback", "always"] = "always"
    video_extensions: list[str] = Field(
        default_factory=lambda: [".mp4", ".webm", ".mov", ".mkv", ".m4v", ".avi"]
    )
    remove_from_input: bool = True


class MediaConfig(_Base):
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    frames_per_video: int = 6
    frames_per_gif: int = 6
    max_video_duration_s: float = 180
    frame_max_total_pixels: int = 4_500_000


class TallImageConfig(_Base):
    enabled: bool = True
    min_aspect: float = 2.5
    min_long_side: int = 1600
    max_tiles: int = 4
    overlap_px: int = 40
    max_total_pixels: int = 4_500_000


class ImageConfig(_Base):
    output_jpeg_quality: int = 92
    copy_jpeg_verbatim: bool = True
    model_max_pixels: int = 1_200_000
    model_max_side: int = 1600
    model_jpeg_quality: int = 88
    tall_image: TallImageConfig = Field(default_factory=TallImageConfig)


class NamingConfig(_Base):
    max_slug_length: int = 80
    drop_leading_articles: bool = True


class NsfwConfig(_Base):
    enabled: bool = True
    name: str = "nsfw"
    folder: str = "00 - NSFW"


class CategorizeConfig(_Base):
    category_count: int = 10
    others_name: str = "others"
    discovery_sample_size: int = 150
    top_tags_in_digest: int = 60
    assign_batch_size: int = 12
    min_confidence: float = 0.5
    workers: int = 1
    max_retries: int = 3
    nsfw: NsfwConfig = Field(default_factory=NsfwConfig)


class LoggingConfig(_Base):
    level: str = "INFO"
    file: bool = True


class Settings(_Base):
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    processing: ProcessingConfig = Field(default_factory=ProcessingConfig)
    media: MediaConfig = Field(default_factory=MediaConfig)
    image: ImageConfig = Field(default_factory=ImageConfig)
    naming: NamingConfig = Field(default_factory=NamingConfig)
    categorize: CategorizeConfig = Field(default_factory=CategorizeConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    # Settings for the separate `atomik-meme-web` server (see atomik_meme_web.config.WebConfig).
    # Declared here -- rather than left as an "unknown" extra key -- purely so the `atomik-meme`
    # CLI doesn't warn about it; the CLI itself never reads this field. Kept as a plain,
    # unvalidated dict (not a nested model) so this file has exactly one place that knows the
    # `web:` schema.
    web: dict[str, Any] | None = None

    _source: str = PrivateAttr(default="<built-in defaults>")

    @property
    def source(self) -> str:
        return self._source

    def vision_model(self, profile: str) -> ModelSpec:
        return self.models.vision.accurate if profile == "accurate" else self.models.vision.fast

    def text_model(self, profile: str) -> ModelSpec:
        return self.models.text.accurate if profile == "accurate" else self.models.text.fast


def _user_config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME / "config.yaml"
    return Path.home() / ".config" / APP_NAME / "config.yaml"


def resolve_config_path(explicit: Path | None) -> Path | None:
    """Return the config file that would be used, or None for built-in defaults."""
    if explicit is not None:
        return explicit if explicit.is_file() else explicit
    cwd_config = Path.cwd() / "atomik-meme.yaml"
    if cwd_config.is_file():
        return cwd_config
    user_config = _user_config_path()
    if user_config.is_file():
        return user_config
    return None


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _collect_unknown_keys(model: BaseModel, prefix: str = "") -> list[str]:
    unknown = []
    extra = model.model_extra or {}
    for key in extra:
        unknown.append(f"{prefix}{key}")
    for name in model.__class__.model_fields:
        value = getattr(model, name)
        if isinstance(value, BaseModel):
            unknown.extend(_collect_unknown_keys(value, prefix=f"{prefix}{name}."))
    return unknown


def normalize_ollama_host(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    if "://" not in value:
        value = f"http://{value}"
    return value.rstrip("/")


def load_settings(path: Path | None = None) -> Settings:
    """Resolve, load, deep-merge, and validate settings.

    Order: explicit `path` -> ./atomik-meme.yaml -> %APPDATA%/atomik-meme/config.yaml
    (or ~/.config/atomik-meme/config.yaml) -> built-in defaults.
    """
    defaults = yaml.safe_load(SAMPLE_YAML)
    source = "<built-in defaults>"
    user_data: dict[str, Any] = {}

    candidate: Path | None
    if path is not None:
        if not path.is_file():
            raise SettingsError(f"Config file not found: {path}")
        candidate = path
    else:
        cwd_config = Path.cwd() / "atomik-meme.yaml"
        user_config = _user_config_path()
        if cwd_config.is_file():
            candidate = cwd_config
        elif user_config.is_file():
            candidate = user_config
        else:
            candidate = None

    if candidate is not None:
        try:
            with open(candidate, encoding="utf-8") as fh:
                loaded = yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            raise SettingsError(f"Invalid YAML in {candidate}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise SettingsError(f"Config file {candidate} must contain a YAML mapping")
        user_data = loaded
        source = str(candidate)

    merged = _deep_merge(defaults, user_data)

    try:
        settings = Settings.model_validate(merged)
    except ValidationError as exc:
        raise SettingsError(f"Invalid config: {exc}") from exc

    for key in _collect_unknown_keys(settings):
        logger.warning("Unknown config key ignored: %s", key)

    env_host = os.environ.get("OLLAMA_HOST")
    if env_host:
        settings.ollama.host = normalize_ollama_host(env_host)

    settings._source = source
    return settings


def write_sample_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SAMPLE_YAML, encoding="utf-8")
