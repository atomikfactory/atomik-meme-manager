# atomik-meme

Analyse, name, and categorise a folder of meme images, GIFs, and videos using a local
[Ollama](https://ollama.com) vision model. No cloud calls, no API keys — everything runs against
an Ollama server on your own machine (or LAN).

For each meme, `atomik-meme`:

1. Detects its media type (`image`, `gif`, or `video` — see [Media types](#media-types) below) and
   copies it into an output folder: images as JPEG (verbatim copy for JPEG sources), gifs/videos
   verbatim under their original (lowercased) extension.
2. Asks a local vision model to describe it — for a still image, the image itself; for a gif/video,
   a handful of frames sampled evenly across it — producing a title, description, OCR text, tags,
   meme type, template, tone, topics, language, NSFW flag, and confidence.
3. Names the file from the title (`guy-staring-at-monitor-in-disbelief.jpg`) and writes a JSON
   sidecar with the full analysis next to it.
4. Routes it directly into a pinned category folder when one is known without the model — an
   explicit `--category` hint, an input subfolder name, or the NSFW auto-route rule (see
   [Pinned categories, hints, and NSFW](#pinned-categories-hints-and-nsfw)) — otherwise leaves it at
   OUTPUT's root, awaiting `categorize`.
5. Optionally groups the rest of the collection into up to 10 discovered category folders using a
   second pass with a text model.

## Install

### With `uv` (recommended)

```powershell
uv venv
uv pip install -e ".[dev]"
uv run atomik-meme --help
```

### With plain `pip`

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
atomik-meme --help
```

### Requirements

- Python 3.11+, Ollama reachable somewhere (local or LAN).
- **ffmpeg and ffprobe on PATH** (or pointed at via `media.ffmpeg_path`/`media.ffprobe_path` in
  config) — required for **video** frame sampling and probing (duration/fps/frame count/audio
  presence). Not needed for images or gifs, which are handled entirely by Pillow. `atomik-meme
  check` reports the versions it finds (or `not found`); a missing ffmpeg fails each affected video
  individually with a clear message rather than aborting the batch or affecting images/gifs.

## Quick start

```powershell
# 1. Write a config file you can edit (or skip this and rely on defaults):
uv run atomik-meme init-config atomik-meme.yaml

# 2. Make sure Ollama is reachable and the models you need are installed:
uv run atomik-meme check
uv run atomik-meme models pull --profile fast

# 3. See what would happen, without calling the model:
uv run atomik-meme process .\input .\output --dry-run

# 4. Do it for real, then group into categories:
uv run atomik-meme run .\input .\output
```

## Media types

| `media_type` | Detected from | Stored as | Analysed via |
|---|---|---|---|
| `image` | `.jpg .jpeg .png .webp .bmp .tif .tiff` when not animated | `<slug>.jpg` (JPEG sources copied verbatim; everything else re-encoded) | the image itself (or tall-image tiles for multi-panel comics) |
| `gif` | any `.gif` (static or animated — extension decides); an animated `.webp`/`.png` (APNG) | verbatim copy, original extension lowercased (`<slug>.gif`, `.webp`, `.png`) | `media.frames_per_gif` frames, evenly spaced including the first and last, via Pillow; `duration_s`/`fps`/`frame_count` come from the gif's own per-frame timing (`has_audio` stays null) |
| `video` | `processing.video_extensions` (default `.mp4 .webm .mov .mkv .m4v .avi`) | verbatim copy, original extension lowercased | `media.frames_per_video` frames at evenly spaced timestamps (via ffmpeg), plus duration/fps/frame-count/audio-presence probed via ffprobe |

Notes:

- A static (single-frame) `.gif` is still `media_type: gif` — the extension decides, not whether it
  animates. An animated `.webp`/APNG is `gif`; a static one is `image`.
- Frames sent to the model in one request (gif or video) are resized so their **combined** pixel
  count stays under `media.frame_max_total_pixels` (default 4,500,000, the same budget tall-image
  tiling uses) and each individual frame stays under `image.model_max_pixels`; frames are never
  upscaled and always sent in chronological order. The vision prompt asks for a description of the
  meme *as a whole* — what happens over time and why it's funny — not a frame-by-frame narration.
- Videos longer than `media.max_video_duration_s` (default 180 s) are still processed from sampled
  frames; the sidecar gets `file.over_duration_limit: true` and a warning is logged.
- Collision detection on the output filename stem is extension-agnostic: a gif and an image can't
  end up sharing a stem even though their extensions differ.
- sha256 identity, duplicate detection, and resume semantics (below) are unchanged and apply
  uniformly across all three media types; hashing streams the file, so large videos are fine.

## Output layout

```text
output/
├── 00 - NSFW/                  image/  gif/  video/     # pinned, id 0, folder name from config
├── 01-<category>/ … 10-others/ each with image/ gif/ video/ subfolders (created only when needed)
├── 11-<user-category>/ …       pinned, created from a processing hint
├── <slug>.jpg / .gif / .mp4    uncategorised pairs stay flat at OUTPUT's root until `categorize`
└── categories.json             "layout_version": 2; each category also carries pinned/rule/source
```

A meme is placed directly into `<category folder>/<image|gif|video>/` at `process` time when its
category is already known without the model (an explicit hint or the NSFW rule — see below);
otherwise it stays flat at the root and `categorize` moves it later. Running `categorize` also
normalises layout as a side effect: any pair it finds sitting directly inside a category folder
(the v1 layout, or one moved there by hand) is moved into the right `<image|gif|video>/`
subfolder. To normalise an existing collection **without** any LLM calls, run:

```powershell
uv run atomik-meme migrate .\output
```

`migrate` also registers the pinned `00 - NSFW` category (adopting whatever folder already exists
on disk) when `categorize.nsfw.enabled`, and rewrites any sidecar still at an older
`schema_version` in place — printing `Sidecars upgraded: N` alongside the pairs-moved count.
`migrate` is idempotent — safe to run repeatedly, and a no-op (0 moved, 0 upgraded) once the
collection is already on the current layout and schema.

## Pinned categories, hints, and NSFW

Most categories (ids 1–10, including `others`) come from `categorize`'s discovery pass and get
renumbered by member count each time discovery reruns. **Pinned** categories are different: they
are never renumbered, never reassigned, and never touched by `--rediscover`.

- **`00 - NSFW`** (id 0): when `categorize.nsfw.enabled` (default `true`), `process`, `categorize`,
  and `migrate` all register this pinned category at the very start of the run if it's missing —
  *before* resolving any hint — adopting whatever `"00 - NSFW"`-named folder already exists on disk
  rather than creating a second one. This is what makes `--category nsfw` reliably resolve to id 0
  instead of accidentally creating a new pinned *user* category by that name. Any meme the vision
  model flags `nsfw: true` is then routed here automatically — at `process` time if it isn't
  otherwise hinted, or at a later `categorize` if it slipped through uncategorised (e.g. the rule
  was off when it was processed). The folder name (`categorize.nsfw.folder`, default
  `"00 - NSFW"`) is used verbatim. Set `categorize.nsfw.enabled: false` to disable the rule
  entirely (NSFW memes are then treated like any other meme, and the category is never
  auto-registered).
- **User categories** (ids 11+): created from a **group hint** the first time it doesn't match an
  existing category. Two ways to hint:
  - `--category NAME` on `process`/`run` applies `NAME` to every file in that run.
  - Without a flag, a file sitting below a first-level INPUT subfolder (`input/<name>/…`, any
    depth) is hinted with that folder's name — handy for pre-sorted dumps.
  - The CLI flag wins over a subfolder hint. Matching is by slug against a category's name, its
    folder minus the `NN-` prefix, or its full folder slug (case-insensitive, no fuzzy matching) —
    so `nsfw`, `NSFW`, and `"00 - NSFW"` all resolve to the NSFW category, and `"Office Software
    Humor"` resolves to an existing `office-software-humor` category. No match → a new pinned
    category is created (next free id ≥ 11) and `categories.json` is written immediately, before
    processing starts. Its `name` is stored as the hint's *slug* (e.g. `office-software-humor`,
    matching how it's looked up), not the raw text typed; the original text is preserved in the
    category's `description` instead.
  - A hint always wins over the NSFW rule for that meme.
  - Hinted/ruled memes are stamped `category.source: "hint"` / `"rule"` (discovered ones are
    `"llm"`) and are placed directly into their folder — `categorize` never touches them again.
- `categorize`'s discovery digest and assignment step both **exclude** pinned/NSFW memes entirely —
  they don't influence what categories get invented, and are never sent to the model for
  assignment. The discovery prompt is told about existing pinned categories by name so it doesn't
  invent a duplicate.
- `count` is maintained for every category, pinned or not.
- The run summary prints `Hinted: N` and, when any pinned category was newly registered this run
  (a hint, or the NSFW rule), a `Created categories: <folder>, …` line; the run-summary JSON's
  `categories_created` lists each as `{id, name, folder}` (an empty list when none were created).

## Model recommendation (RTX 3060 Ti, 8 GB VRAM)

`atomik-meme` uses **Qwen3.5**, Alibaba's unified multimodal family (native vision + text in one
model), so the same download serves both the vision and text/categorisation roles for a profile.

| Profile | Role | Model tag | Download | VRAM @ 8k ctx | Measured speed (this machine) | Notes |
|---|---|---|---|---|---|---|
| `fast` | vision + text | `qwen3.5:4b` (Q4_K_M) | 3.4 GB | ~5 GB | cold load ~20 s, then **~4 s/meme** (~77 tok/s), 100% GPU | Default. Fits fully on GPU with headroom. Excellent OCR for its size. |
| `accurate` | vision + text | `qwen3.5:9b` (Q4_K_M) | 6.6 GB | ~7.5–8 GB | cold load ~7 s, then **~7 s/meme** (~44 tok/s), ~88% GPU / ~12% CPU when ~3 GB VRAM is already used by other apps (fits fully on an otherwise-idle GPU) | Better category naming and assignment consistency. |

Both models accept images + a JSON-schema `format` + `think: false` over `/api/chat` — this is the
only API surface `atomik-meme` uses. Every request also sets `num_predict` (1200 for vision,
4096 for text/categorisation by default, both overridable per model in config): without a cap, a
small model that falls into a repetition loop inside a JSON string keeps generating until
`num_ctx` is exhausted (observed: 97 s for a single image) instead of failing fast.

Alternatives (drop in via config, see [Configuration](#configuration)):

- `qwen3-vl:4b` / `qwen3-vl:8b` — dedicated Qwen3-VL instruct models (3.3 GB / 6.1 GB), slightly
  smaller than Qwen3.5 at the same parameter count. Use if Qwen3.5 misbehaves on your Ollama
  version.
- `qwen2.5vl:3b` / `qwen2.5vl:7b` — older generation, fallback only.
- Text-only role: `qwen3:4b`, `qwen3:8b`, or an already-installed model such as `qwen2.5:7b`.

Quantisation notes: Q4_K_M is the right choice for 8 GB VRAM. Q8 variants of the 4B model
(5.1–5.3 GB) are a reasonable "accurate-lite" option; anything 9B-and-up at Q8 will not fit.
Thinking mode (`think`) is off by default for both roles — it multiplies latency 3–10x and adds
nothing to structured extraction — but is exposed in config for the category-discovery step.

## Using the repo's `models` folder

You can keep this project's models in a repo-local `models/` directory, so downloads for this
project don't mix into your normal Ollama installation. The folder is not tracked in git —
`scripts/ollama-serve-local.ps1` creates it on first run. Two ways to use it:

**Option A — a second, dedicated Ollama server (recommended, does not touch your normal Ollama):**

```powershell
# Terminal 1 - leave running:
.\scripts\ollama-serve-local.ps1        # OLLAMA_MODELS=<repo>\models, OLLAMA_HOST=127.0.0.1:11435

# Terminal 2 - one-off:
.\scripts\ollama-pull-models.ps1        # pulls the models named in atomik-meme.yaml (or the defaults)
```

Then point `atomik-meme` at it, either in `atomik-meme.yaml`:

```yaml
ollama:
  host: http://127.0.0.1:11435
```

or per-invocation via the environment variable (which always overrides the config file):

```powershell
$env:OLLAMA_HOST = "127.0.0.1:11435"
uv run atomik-meme check
```

**Option B — point your normal Ollama installation at this folder globally:**

```powershell
$env:OLLAMA_MODELS = "<path-to-this-repo>\models"
# restart the Ollama tray app / service so it picks up the new model store
ollama pull qwen3.5:4b
ollama pull qwen3.5:9b
```

This makes your one normal Ollama server (port 11434) use the repo's model folder for *every*
model, which is simpler but means the model store is no longer separate from your day-to-day use
of Ollama.

## CLI reference

Global options (before the subcommand): `--config PATH`, `-v`/`--verbose`, `--log-file PATH`.

```text
atomik-meme process INPUT OUTPUT [--model fast|accurate] [--vision-model TAG]
                                   [--workers N] [--force] [--limit N] [--dry-run]
                                   [--recursive/--no-recursive] [--categorize]
                                   [--ocr-pass off|fallback|always]
                                   [--category NAME] [--consume/--no-consume]

atomik-meme categorize OUTPUT    [--model fast|accurate] [--text-model TAG]
                                   [--rediscover] [--dry-run] [--workers N]

atomik-meme run INPUT OUTPUT     # process then categorize; accepts the union of both commands'
                                   # options, including --ocr-pass, --category, --consume

atomik-meme migrate OUTPUT       # normalise an existing collection to the v2 layout; no LLM calls

atomik-meme check                # Ollama reachable? models present? ffmpeg/ffprobe? GPU? config?

atomik-meme models list|pull [--profile fast|accurate|all]

atomik-meme search OUTPUT QUERY  [--tag T]... [--category C] [--type T] [--media image|gif|video]
                                   [--json]

atomik-meme init-config [PATH]   # write the sample config (default ./atomik-meme.yaml)
```

### Examples

```powershell
# Preview what would happen - no Ollama calls, no files written:
uv run atomik-meme process .\input .\output --dry-run

# Process with the accurate profile, 2 workers:
uv run atomik-meme process .\input .\output --model accurate --workers 2

# Re-run: already-processed files are skipped automatically (see Resume, below):
uv run atomik-meme process .\input .\output

# Force re-analysis of everything, even if already indexed:
uv run atomik-meme process .\input .\output --force

# Group the collection into categories:
uv run atomik-meme categorize .\output

# Only re-run category *discovery* (and reassign everything) when the collection has changed a lot:
uv run atomik-meme categorize .\output --rediscover

# Process + categorize in one go:
uv run atomik-meme run .\input .\output

# Find memes:
uv run atomik-meme search .\output "cat" --tag programming
uv run atomik-meme search .\output "" --category reaction --json
uv run atomik-meme search .\output "" --media video

# Inspect/pull models:
uv run atomik-meme models list
uv run atomik-meme models pull --profile accurate

# Force a dedicated OCR pass for a batch of text-heavy scans/comics:
uv run atomik-meme process .\input .\output --ocr-pass always

# Sort a pre-organised dump straight into named categories (creates them if new):
uv run atomik-meme process .\dump\reaction-shots .\output --category reaction

# Process and delete verified sources from INPUT afterwards:
uv run atomik-meme process .\input .\output --consume

# Bring an existing v1 collection up to the v2 <category>/<image|gif|video>/ layout:
uv run atomik-meme migrate .\output
```

`--workers` must be `>= 1` (omit it to use `processing.workers` from config); `--workers 0` is
rejected as a usage error (exit 1) rather than silently falling back to a default.

### Consuming the input (`--consume`)

By default (`processing.remove_from_input: true`) `process`/`run` consume the input: a source
file is deleted only after its output pair has been written **and**
verified — read back through `Sidecar.read` — so a crash or a failed write never loses the only
copy of a meme. Sources skipped as "already done" or "duplicate" are also removed, but only once
the indexed output pair is confirmed to exist on disk; a **failed** source is never removed, and
`--dry-run` never removes anything (it lists what *would* be removed). Input subfolders left empty
afterwards are removed too — never the input root itself. Use `--no-consume` (or set the key to `false`)
to keep the sources.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success. |
| 1 | Usage or config error (bad path, malformed/invalid config file, `--workers 0`). |
| 2 | Ollama unreachable, or a required model is missing and `auto_pull` is off. |
| 3 | The batch completed but one or more items failed (see the printed `Failures:` list and `failures.json`). |

`process --categorize` and `run` fold both phases into one exit code: 2 if either phase can't
reach Ollama or is missing a model, otherwise 3 if processing had failures *or* the categorize
phase itself failed (e.g. discovery couldn't produce valid output after all retries), otherwise 0.
The two phases still each write their own `runs/<timestamp>-<command>.json` summary; both paths
are printed.

## Configuration

Resolution order: `--config PATH` → `./atomik-meme.yaml` → `%APPDATA%\atomik-meme\config.yaml`
(or `~/.config/atomik-meme/config.yaml`) → built-in defaults. Missing keys fall back to defaults
(deep merge), unknown keys are logged as warnings (not fatal), and wrong types raise a clear error.
The environment variable `OLLAMA_HOST` always overrides `ollama.host` — bare `host:port` forms
(no `http://`) are accepted, matching Ollama's own CLI convention.

Write the commented sample with `atomik-meme init-config`, or see
[`atomik-meme.example.yaml`](atomik-meme.example.yaml) at the repo root. Key sections:

```yaml
ollama:
  host: http://127.0.0.1:11434
  timeout_s: 180
  retries: 2
  keep_alive: 10m
  auto_pull: false
  default_profile: fast

models:
  vision: { fast: {...}, accurate: {...} }
  text:   { fast: {...}, accurate: {...} }

processing:
  workers: 1
  recursive: true
  extensions: [.jpg, .jpeg, .png, .webp, .gif, .bmp, .tiff, .tif]
  shutdown_grace_s: 30
  ocr_pass: always           # off | fallback | always - see "OCR pass" below
  video_extensions: [.mp4, .webm, .mov, .mkv, .m4v, .avi]
  remove_from_input: true   # --consume/--no-consume overrides this per run

media:
  ffmpeg_path: ffmpeg
  ffprobe_path: ffprobe
  frames_per_video: 6
  frames_per_gif: 6
  max_video_duration_s: 180
  frame_max_total_pixels: 4500000

image:
  output_jpeg_quality: 92
  copy_jpeg_verbatim: true
  model_max_pixels: 1200000
  model_max_side: 1600       # tiles ignore this - see note below
  model_jpeg_quality: 88
  tall_image:
    enabled: true
    min_aspect: 2.5
    min_long_side: 1600
    max_tiles: 4
    overlap_px: 40
    max_total_pixels: 4500000   # combined pixel budget across all tiles in one request

naming:
  max_slug_length: 80
  drop_leading_articles: true

categorize:
  category_count: 10
  others_name: others
  discovery_sample_size: 150
  top_tags_in_digest: 60
  assign_batch_size: 12
  min_confidence: 0.5
  workers: 1
  max_retries: 3
  nsfw:                      # see "Pinned categories, hints, and NSFW" above
    enabled: true
    name: nsfw
    folder: "00 - NSFW"

logging:
  level: INFO    # root logger + the log FILE handler; the console stays at WARNING (-v -> INFO)
  file: true
```

`image.tall_image.max_total_pixels` matters for multi-panel comics: a naively-tiled tall image can
send thousands of prompt tokens (a raw 544x9652 comic measured ~4,167 tokens against Qwen3.5,
roughly 1 token per ~1,250 px) — dangerous against an 8k context window. After tiling, every tile
is scaled down *together* (never individually distorted, and never below what fits `model_max_pixels`
per tile) so the *combined* pixel count across all tiles in one request never exceeds this budget.

Tiles deliberately ignore `model_max_side`: a comic strip's long side isn't what limits
readability, its width is - so a 544 px wide comic keeps close to its native width instead of
being squeezed down to fit a "single image" side limit meant for normal memes. `model_max_side`
still applies to everything that isn't tiled.

`logging.level` sets the level of the root logger and of the log file handler (whether it's the
one under `OUTPUT/.meme-manager/logs/` or an explicit `--log-file`). The console handler is
independent of it - it's always WARNING, or INFO with `-v`/`--verbose` - but in practice the
console can't show more than the root logger lets through, so setting `logging.level` more
restrictive than WARNING (e.g. `ERROR`) will also quiet console output. Leave it at `DEBUG`,
`INFO`, or `WARNING` for normal use and rely on `-v` for extra console detail.

### OCR pass (`processing.ocr_pass`)

Measured on `qwen3.5:4b`: `ocr_text` embedded in the single combined analysis call came back
**empty on about 2 of 3 runs** for tall/text-heavy images, regardless of how the prompt was
worded. A dedicated, OCR-only call (no other fields to produce, nothing else competing for the
model's attention) succeeded **6 of 6** times on the same images, with longer and more consistent
transcripts, at a cost of roughly 1-2.5 s each. `processing.ocr_pass` controls how that trade-off
is made, and can be overridden per run with `--ocr-pass`:

| Mode | Behaviour | Cost |
|---|---|---|
| `off` | Single combined analysis call only. | Fastest; least reliable OCR. |
| `fallback` | The OCR-only call runs *only if* the analysis call came back with an empty `ocr_text`, and its result replaces it. | ~+0 s for memes already read correctly, ~+1-2 s otherwise. |
| `always` (default) | OCR runs first; if it finds text, that transcript is handed to the analysis call as a hint ("A separate OCR pass read the following text..."). The OCR transcript also replaces the analysis call's own `ocr_text`, which small models leave empty ~2 of 3 times on text-heavy images. | ~+2 s per image; best quality. |

The sidecar's `processing.ocr_pass` field records `"fallback"`/`"always"` when a dedicated OCR
call actually ran for that image, or `null` if it didn't (including `fallback` mode when the
analysis call already returned non-empty text, and `off` mode always).

## Resume and duplicate semantics

- `process` never modifies INPUT — it only reads from it.
- Every source file is hashed (SHA-256, streamed). Re-running `process` on the same INPUT/OUTPUT
  is a no-op for sources already recorded in `OUTPUT/.meme-manager/index.json` (or found by
  rescanning sidecars, if the index is missing/corrupt) — these are counted as **"already done"**.
- If two files in the same run have identical bytes, only the first is processed; the rest are
  counted as **"duplicates"**.
- `--force` reprocesses sources already marked "done" in the index, but two identical files within
  the same run are still deduplicated (there is exactly one sidecar per content hash). If the new
  analysis produces a different title (a different filename), the new pair is written into
  whatever folder the old pair lived in (so a re-analysed, already-categorised meme doesn't fall
  out of its category folder back to OUTPUT's root), the old sidecar's `category` is carried over
  onto the new one, and the old pair is deleted only once the new one is safely on disk - never
  two pairs for the same source, and never an orphan left behind.
- A crash or Ctrl-C loses at most the in-flight item: the index is flushed atomically every 10
  items and at the end, and `index.json` is fully rebuildable from the sidecars on disk if it is
  ever missing or corrupted. Even when `index.json` loads fine, every `process`/`categorize` run
  also reconciles it against a walk of OUTPUT first, adding an entry for any sidecar it finds that
  the (possibly stale, e.g. from a crash between two periodic flushes) index doesn't know about
  yet - so a hard crash can never cause a later run to reprocess a file or create a colliding
  duplicate for it.

## Categorisation semantics

`categorize` groups everything under OUTPUT into at most 10 discovered folders (`01-<name>` …
`10-others`), each with `image/`/`gif/`/`video/` subfolders. Pinned categories (`00 - NSFW`, and
any `11+` user categories created from hints — see above) are excluded entirely from this process:
they never appear in the discovery digest, are never assigned to by the model, are never
renumbered, and `--rediscover` never touches their members.

- **First run** (no `categories.json` yet), or with **`--rediscover`**: the text model invents 9
  categories from a digest of the whole collection (tag/topic/type/tone statistics plus a
  representative sample), `others` is added as the 10th, every meme is (re)assigned, folders are
  numbered 1–9 by descending member count (ties broken by the model's original ordering), and any
  now-empty `NN-*` folders from a previous layout are removed.
- **Subsequent runs** (`categories.json` exists, no `--rediscover`) run in **assign-only mode**:
  the existing categories and their numbering/folders are left untouched; only memes with no
  category yet (or filed in the wrong folder) are assigned and moved. This is idempotent — running
  it again with nothing new to categorise moves nothing.
- A meme whose assignment confidence falls below `categorize.min_confidence` is placed in
  `others`, with the model's original guess preserved in `category.suggested`.
- `--dry-run` runs discovery/assignment exactly as a real run would (so the preview is accurate)
  but prints the proposed categories, counts, and up to 5 example titles each, without moving or
  writing anything.
- If a single item fails while applying its category (a filesystem error moving its pair, etc.),
  it's recorded as a failure (stage `move`) and the rest of the batch still proceeds -
  `categories.json` and the index are still written for everything that succeeded. If a sidecar's
  media file has gone missing (e.g. manual deletion) but its `.json` is still there, that's logged
  as a warning naming the sidecar, and the sidecar is still moved to its category folder.
- `category_id` values from the model are validated against the actual set of categories for this
  run before anything is applied; an unknown or out-of-range id is treated the same as an
  unassigned one - routed to `others` with the model's original id kept in `category.suggested` -
  rather than ever crashing the run.

## Performance tips

- Default `processing.workers: 1`. Raise to 2 only with the 4B model and
  `OLLAMA_NUM_PARALLEL=2` set on the Ollama server — more parallelism against 8 GB of VRAM
  typically just causes model reloads and *lower* total throughput, not higher.
- Leave VRAM headroom: the 9B/accurate model needs the GPU mostly idle to avoid CPU spillover
  (see the measured numbers above — spillover on a partly-loaded GPU roughly doubles latency).
- `ollama.keep_alive` (default `10m`) keeps the model loaded between requests; the first request
  of a batch already gets `timeout_s * 2` to absorb a cold model load (10–60 s).
- JPEG sources are never re-encoded (`copy_jpeg_verbatim: true`), and images are never upscaled for
  the model — both save time and quality loss for no benefit.

## Troubleshooting

- **"Model 'X' is not installed"** (exit 2): run the `ollama pull` command printed in the error, or
  set `ollama.auto_pull: true` to have `atomik-meme` pull it automatically before the batch.
- **Timeouts under load**: raise `ollama.timeout_s`, reduce `processing.workers` to 1, and check
  `atomik-meme check` for GPU/VRAM headroom.
- **VRAM spill / everything gets slow**: another process (browser, game, your own Ollama tray app
  with a different model loaded) is holding VRAM. Close it, or use `--model fast`.
- **Wrong Ollama server**: `atomik-meme check` prints the effective host, version, and whether
  each configured model is installed — start there. Remember `OLLAMA_HOST` always overrides
  `ollama.host` from the config file.
- **A video fails with a `decode` error naming `media.ffmpeg_path`/`media.ffprobe_path`**: ffmpeg
  or ffprobe couldn't be run at all (not installed, or not on PATH). Run `atomik-meme check` — it
  prints `ffmpeg: <version>` / `ffprobe: <version>` or `not found` for each, independently of
  whether Ollama is reachable. Install ffmpeg (e.g. via your package manager, or
  <https://ffmpeg.org/download.html> on Windows) or set `media.ffmpeg_path`/`media.ffprobe_path` to
  the full executable paths. Images and gifs are unaffected either way — only videos need ffmpeg.
- **Console shows `?` or garbled characters**: this shouldn't happen — stdout/stderr are
  reconfigured to UTF-8 at startup — but if it does, your terminal's own code page is the cause,
  not `atomik-meme`; the sidecar JSON files are always UTF-8 regardless.
- **"Failed to initialize samplers: failed to parse grammar" (HTTP 400 from Ollama)**: this is
  Ollama's llama.cpp backend choking while converting the JSON Schema handed to `format` into a
  sampling grammar - observed specifically with `minLength`/`maxLength` constraints (e.g.
  `maxLength: 2000` on a string field). `atomik-meme` already strips the keywords known to
  trigger this (`schema.GRAMMAR_UNSAFE_KEYWORDS`: `minLength`, `maxLength`, `pattern`, `format`,
  `default`, `title`, `examples`) before sending any schema, and enforces those same limits
  itself afterwards (clipping over-long values rather than failing validation). If you see this
  error anyway - e.g. after editing a schema, or against a very different Ollama/llama.cpp
  version - it means some other keyword in the schema needs the same treatment; file an issue
  with the exact schema and Ollama version.

## Metadata schema

Each meme gets a `<stem>.json` sidecar next to its `<stem>.jpg`/`.gif`/`.mp4`/etc. (`schema_version`
is now `2`; sidecars written by v1 still load fine — the new fields default sensibly):

```jsonc
{
  "schema_version": 2,
  "id": "3f9a1c2b7d4e5f60",
  "file": { "name": "guy-staring-at-monitor-in-disbelief.jpg", "width": 460, "height": 795,
            "bytes": 48213, "format": "jpeg", "animated": false,
            // v2: media_type is "image" | "gif" | "video"; the rest are gif/video-only (null
            // for images), and over_duration_limit is only ever true for an over-long video.
            "media_type": "image", "duration_s": null, "fps": null, "frame_count": null,
            "has_audio": null, "over_duration_limit": false },
  "source": { "original_filename": "IMG_48392.png", "original_path": "D:/memes/input/IMG_48392.png",
              "sha256": "3f9a1c2b...", "format": "png", "width": 460, "height": 795,
              "bytes": 51000, "converted": true },
  "analysis": {
    "title": "Guy staring at monitor in disbelief",
    "description": "A man leans toward his monitor with a shocked expression after reading an error message.",
    "ocr_text": "WHY IS IT NOT WORKING",
    "tags": ["programming", "frustration", "reaction", "computer", "error"],
    "subjects": ["man", "computer monitor"],
    "meme_type": "reaction",
    "template": null,
    "tone": ["funny", "frustrated"],
    "topics": ["programming", "work"],
    "language": "en",
    "nsfw": false,
    "confidence": 0.86
  },
  "category": { "id": 4, "name": "programming", "confidence": 0.9,
                "assigned_at": "2026-09-16T20:11:03Z", "model": "qwen3.5:4b", "run_id": "a1b2c3d4e5f6",
                "suggested": null, "source": "llm" },  // v2: source is "llm" | "hint" | "rule"
  "processing": {
    "tool_version": "0.1.0", "profile": "fast", "vision_model": "qwen3.5:4b",
    "ollama_version": "0.32.5", "prompt_version": 3, "processed_at": "2026-09-16T20:03:41Z",
    "duration_s": 4.2,
    "image_sent": { "width": 460, "height": 795, "tiles": 1, "frames": 1 },  // v2 adds "frames"
    "attempts": 1, "ocr_pass": null
  }
}
```

`processing.ocr_pass` is `"fallback"` or `"always"` when a dedicated OCR-only call ran for this
image (see [OCR pass](#ocr-pass-processingocr_pass) above), or `null` if it didn't.

`processing.image_sent.tiles` is the tall-image tiling count (image pipeline only, always 1
otherwise); `.frames` is the number of gif/video frames actually sampled and sent (always 1 for the
image pipeline). `category.source` records how a meme got its category: `"llm"` for a normal
`categorize` assignment, `"hint"` for `--category`/subfolder routing, `"rule"` for the NSFW
auto-route.

`categories.json` also gained `"layout_version": 2` and, per category, `"pinned"`, `"rule"`
(`"nsfw"` or `null`), and `"source"` (`"llm" | "user" | "rule"`) — see
[Pinned categories, hints, and NSFW](#pinned-categories-hints-and-nsfw).

`OUTPUT/.meme-manager/` holds `index.json` (cache, always rebuildable), `failures.json` (last
run's failures), `runs/<timestamp>-<command>.json` (per-run summaries), and `logs/` (rotating log
files, 5 x 5 MB, when `logging.file: true`).

## Web UI

A local, read-only browser UI/API for browsing an already-processed collection — search,
filters, a virtualised gallery, favorites, a full-screen viewer, duplicate detection, and
drag-and-drop into `input/` — served by its own `atomik-meme-web` command. It never modifies, moves,
renames, or deletes anything in the library; its only writes are its own data directory
(`index.db`, `thumbs/`) and, if enabled, the inbox drop.

### Install

`atomik-meme-web`'s dependencies (`fastapi`, `uvicorn`, `python-multipart`, `rapidfuzz`) are part of the
same package, so the usual install covers it:

```powershell
uv pip install -e ".[dev]"
uv run atomik-meme-web --help
```

The frontend (`web/`) is a separate Svelte/Vite project built to static files the server serves
directly; the backend and its tests never require Node. Build it once with:

```powershell
scripts\build-web.ps1
# equivalent to:  cd web && npm ci && npm run build
```

If `web/dist` hasn't been built yet, `atomik-meme-web` still serves the full API (and `/docs`) and shows
a friendly placeholder page at `/` explaining how to build the UI.

### Run

```powershell
uv run atomik-meme-web                                  # serve OUTPUT (./output) at http://127.0.0.1:8765, scan, open a browser tab
uv run atomik-meme-web --library D:\memes --port 9000 --no-browser
uv run atomik-meme-web index                            # one-off scan: prints added/modified/removed/moved, exits
uv run atomik-meme-web index --rebuild                  # escape hatch: rebuild the index from scratch (see below)
```

Other flags: `--data-dir` (default `<library>/.meme-web`), `--no-scan` (skip the scan on start),
`--config` (explicit `atomik-meme.yaml` path). The server rescans on start (unless `--no-scan`),
on a timer (`web.scan_interval_min`, default every 5 minutes; `0` disables it), and on demand via
the UI's rescan button (`POST /api/scan`).

`index --rebuild` drops and recreates the `items`/`items_fts`/`item_tags` tables before running a
full scan — favorites, view counts, and saved collections are untouched (they're keyed by the
file's content hash, not by database row id), so nothing you've starred or saved is lost. Derived
lookup tables like `item_tags` are normally kept in sync automatically as files are scanned; this
is a manual repair tool for the rare case where they've drifted out of sync with the library (a
routine `POST /api/scan`/`atomik-meme-web index` never touches a file it considers unchanged, so it can't
by itself repair a table that got out of sync some other way, e.g. an older database opened by a
newer version of `atomik-meme-web`).

### Choosing a library

You don't have to restart the server to point it at a different folder — click the library chip
in the top bar (or press `Ctrl+O`) to open the library panel, which offers three ways to pick a
folder, all ending in the same switch:

1. **Choose folder…** — the OS's native "select a folder" dialog (disabled with a hint if Tk
   isn't available on this machine).
2. **Browse…** — an in-app folder tree (drives → subfolders, lazily loaded) with a breadcrumb
   and a "Use this folder" button.
3. **Type a path** — paste or type an absolute path and hit Switch.

Switching cancels any scan in progress, builds a fresh index for the new folder (never touching
the old one), and starts scanning it; the panel shows progress and the gallery refreshes when
it's done. Favorites/view counts/saved collections are per-library (each gets its own
`<library>/.meme-web/` — or a per-library subfolder under `web.data_dir` if that's set explicitly
in the config, so two libraries never share one index) and reappear exactly as you left them if
you switch back. A "Recent libraries" list remembers your last 10 folders.

At startup, the library is resolved in this order — first match wins:

1. `--library` on the command line
2. the last library you switched to (persisted across restarts; see `web.remember_library` below)
3. `web.library` in `atomik-meme.yaml`, if that folder still exists
4. `./output`, if it exists
5. otherwise the server starts with **no library selected** — the UI shows a full-screen
   "Choose your meme folder" prompt instead of the gallery, and `atomik-meme-web index` (which has no UI
   to fall back on) exits with a clear error telling you to pass `--library`.

The "remembered last library" state lives in `%LOCALAPPDATA%\atomik-meme-web\state.json` on Windows
(`$XDG_STATE_HOME/atomik-meme-web/state.json` or `~/.local/state/atomik-meme-web/state.json` elsewhere); set
`web.remember_library: false` to stop reading and writing it.

### Configuration

`atomik-meme.yaml` gains an optional `web:` section (all keys optional; every `atomik-meme-web` CLI
flag overrides the matching key; this section has no effect on the `atomik-meme` CLI):

```yaml
web:
  library: ./output             # folder to index (default: ./output if it exists, else required)
  data_dir: null                # default: <library>/.meme-web (index.db, thumbs/, cache)
  host: 127.0.0.1
  port: 8765
  open_browser: true
  scan_on_start: true
  scan_interval_min: 5          # 0 disables periodic rescans
  inbox_dir: ./input            # where drag-and-dropped files are saved; null disables drops
  nsfw_default: hide            # hide | blur | show  (initial UI state)
  thumb_sizes: [320, 640]
  thumb_retry_after_min: 60      # cooldown before retrying a thumbnail that failed to generate
  remember_library: true         # persist the last-opened library across restarts
  picker_timeout_s: 600          # how long the native folder-picker dialog may stay open
  extensions:
    image: [.jpg, .jpeg, .png, .webp, .bmp, .tif, .tiff]
    gif:   [.gif]               # + animated .webp/.png detected at probe time
    video: [.mp4, .webm, .mov, .mkv, .m4v, .avi]
  ignore: [".meme-manager", ".meme-web", "node_modules", ".git"]
```

Metadata comes from the same `<stem>.json` sidecars `atomik-meme` writes (title, description,
OCR text, tags, topics, tone, meme type, template, category, NSFW, confidence), plus the
`NN-name` category folder convention (`00 - NSFW` → category `nsfw`) as a fallback when a file
has no sidecar. Search is SQLite FTS5 (trigram tokenizer) with `type:`/`cat:`/`tag:`/`is:fav`/
`is:nsfw` operators and a `rapidfuzz` typo-tolerant fallback; duplicates are detected both
exactly (sha256) and near-exactly (a perceptual dHash on thumbnail generation).

Every non-`GET` request (favoriting, viewing, scanning, dropping files into the inbox, opening
or revealing a file, ...) must carry a custom `X-Atomik-Meme: 1` header, or the server rejects it
with `403`; this is a lightweight CSRF defence (a browser can't attach a custom header to a
cross-site request without a preflight, which the server only answers for the Vite dev origin)
so an unrelated web page can't silently trigger actions against your local server just by you
having it open in another tab. The bundled frontend's fetch wrapper sends this header
automatically; a hand-rolled client (curl, a script, `/docs`'s "Try it out") needs to set it too.

### Dev workflow

The backend and frontend are independent processes during development:

```powershell
# terminal 1 - API only, no browser tab:
uv run atomik-meme-web --no-browser

# terminal 2 - Vite dev server on :5173, proxies /api to :8765, hot reload:
cd web
npm run dev

# frontend work without a running backend - a dependency-free mock API on :8765:
node web/mock/server.mjs
```

Backend tests live in `tests/web/` (`uv run pytest -q tests/web`) and use tmp libraries with
synthetic media (Pillow images/GIFs, an ffmpeg-generated mp4, and real `Sidecar` JSON fixtures) —
they never touch a real `output/`/`input/` folder. Frontend checks (`svelte-check`, `vite build`,
`vitest`) live under `web/`.

## License

GNU General Public License v3.0 or later — see [`LICENSE`](LICENSE).
