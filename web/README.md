# atomik-meme-web (frontend)

Svelte 5 (runes) + Vite + TypeScript single-page app for the atomik-meme local media
library. This directory is self-contained. `src/lib/types.ts` and `src/lib/api.ts` are the
source of truth for every endpoint and field name the app talks to — they mirror the
`atomik-meme-web` backend in `src/atomik_meme_web/` exactly, including the
library-selection endpoints used to choose and switch the library folder.

## Scripts

```
npm ci               # install pinned dependencies (package-lock.json is committed)
npm run dev           # Vite dev server on :5173, proxies /api -> http://127.0.0.1:8765
npm run build         # production build -> web/dist (served by the Python backend)
npm run preview       # preview the production build locally
npm run check         # svelte-check (strict TS + Svelte diagnostics)
npm test              # vitest run (masonry.ts + shortcuts.ts + library panel unit tests)
npm run test:watch    # vitest in watch mode
npm run mock          # dependency-free mock API server on :8765 (see below)
```

Keyboard: `/` or `Ctrl+K` focuses search, `Ctrl+O` opens the library picker (see `?` for the
full shortcut list, or `src/lib/shortcuts.ts`).

## Developing without the Python backend

`mock/server.mjs` is a zero-dependency Node HTTP server that implements every backend
endpoint against ~60 deterministic fixture items (mixed image/gif/video, a few
`nsfw: true`, a few duplicate groups, in-memory favorites/views/collections/scan state). It
reads real files **read-only** from `../output` (recursively, `*.jpg|*.jpeg|*.gif|*.mp4`) and
maps fixtures onto them cyclically so thumbnails/media/video playback are real bytes, not
placeholders (video thumbnails are a small generated SVG placeholder, since real thumbnail
extraction is out of scope for the mock). It never writes into `output/` or the repo-root
`input/` — inbox uploads land in `web/mock/inbox-uploads/` instead.

Typical workflow:

```
# terminal 1
npm run mock
# terminal 2
npm run dev
# open http://localhost:5173
```

The mock also implements the library-selection endpoints:
`GET/POST /api/library`, `POST /api/library/pick` (fake native picker, resolves after ~1s and
cycles through a few fixture paths — every 4th call simulates the user cancelling), `POST
/api/library/reveal`, `DELETE /api/library/recent`, and `GET /api/fs/list` (a small fixed fake
Windows drive tree — deliberately not the real filesystem, so browsing is deterministic across
dev machines). Favorites/views are isolated per switched-to library and restored when you
switch back. Set `MOCK_NO_LIBRARY=1` to start with no library selected, exercising the
full-screen "Choose your meme folder" empty state:

```
MOCK_NO_LIBRARY=1 npm run mock
```

## Layout

- `src/lib/api.ts` — typed fetch client for every `/api/*` endpoint.
- `src/lib/types.ts` — response/request shapes mirroring the backend field names exactly.
- `src/lib/state.svelte.ts` — the app's single rune-based store: search/filters/sort, result
  cache + infinite-scroll paging, selection/lightbox, settings (persisted to `localStorage`),
  toasts, collections, stats, scan polling, library selection (the
  `idle -> picking -> switching -> done/error` panel state machine, see `library.test.ts`), and
  URL sync (`?q=&type=&cat=&tag=&sort=&fav=&nsfw=`).
- `src/lib/masonry.ts` — pure, framework-free virtualised masonry layout math (column count,
  shortest-column placement, incremental append, visible-range selection). Unit-tested.
- `src/lib/shortcuts.ts` — the keyboard shortcut map (+ `Ctrl+O` for the library
  picker), plus the "ignored while typing" rule. Unit-tested.
- `src/lib/itemActions.ts` — favorite/copy-path/copy-media/open/reveal, shared by Card,
  ContextMenu and Lightbox so behaviour + toast copy only lives in one place.
- `src/lib/icons.ts` — maps backend-supplied collection icon names (e.g. `"heart"`) to glyphs
  for the Sidebar. Unit-tested.
- `src/components/` — TopBar (incl. the library chip), FilterBar, Sidebar, Gallery, Card,
  Lightbox, ContextMenu, DuplicatesView, EmptyState, Toasts, ShortcutsHelp, DropZone, StatsBar,
  LibraryPanel (modal: current path/Reveal, Choose folder/Browse/typed path/Recent, switching
  progress view) and LibraryChooser (the shared form inside it, also rendered inline — no modal
  chrome — by the no-library empty state).

## Notes / known deviations from the real backend

- Video items don't get a real extracted-frame thumbnail from the mock server (that requires
  ffmpeg, which is backend/indexer territory) — `/thumb` for a video item returns a small
  generated SVG placeholder instead of raw video bytes, so cards don't show a broken image icon
  at rest. The real backend's ffmpeg-based thumbnails will replace this transparently; no
  frontend change needed.
- Rename/delete of a saved collection uses the browser's native `prompt()`/`confirm()` rather
  than a custom modal, to keep that (infrequent, low-risk, local-only) flow simple.
- The mock keeps the same 60-item fixture pool for every library you switch to (only
  favorites/views are isolated per library, plus a fake per-drive-root "large folder" warning)
  — a real backend switch would index a genuinely different set of files with a different
  total. Good enough to exercise the whole switch/progress/no-op/warning flow without needing a
  second real fixture folder.
- `GET /api/fs/list`'s tree is intentionally fake/fixed (not the real filesystem) for
  deterministic browsing across dev machines, but `POST /api/library` also accepts any path
  that exists for real on disk (via `fs.statSync`) so typing your actual memes folder works
  too, not just fake-tree paths.
