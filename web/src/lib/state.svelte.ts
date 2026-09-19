// Central Svelte 5 rune-based state: search/filter/sort, result cache, selection, settings,
// toasts, collections, scan status and stats. Kept as plain exported functions/objects rather
// than classes to stay easy to reason about.
import { api, ApiError, setApiErrorHandler } from './api';
import type {
  Category,
  CategoryStat,
  Collection,
  DuplicatesResponse,
  FsEntry,
  FsRoot,
  Item,
  LibraryInfo,
  MediaType,
  NsfwUiMode,
  ScanProgress,
  Scan,
  SortKey,
  StatsResponse,
} from './types';

const PAGE_SIZE = 100;
const LS_PREFIX = 'atomik-meme-web:';

function loadJSON<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(LS_PREFIX + key);
    if (raw === null) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function saveJSON(key: string, value: unknown): void {
  try {
    localStorage.setItem(LS_PREFIX + key, JSON.stringify(value));
  } catch {
    // storage full or unavailable; degrade silently
  }
}

// ---------------------------------------------------------------------------
// Settings (persisted)
// ---------------------------------------------------------------------------

export type Theme = 'system' | 'dark' | 'light';

export const settings = $state({
  theme: loadJSON<Theme>('theme', 'system'),
  nsfwMode: loadJSON<NsfwUiMode>('nsfwMode', 'hide'),
  columnMinWidth: loadJSON<number>('columnMinWidth', 220),
  sidebarOpen: loadJSON<boolean>('sidebarOpen', true),
  showTitles: loadJSON<boolean>('showTitles', true),
});

export function setTheme(theme: Theme): void {
  settings.theme = theme;
  saveJSON('theme', theme);
  applyTheme();
}

export function cycleTheme(): void {
  const order: Theme[] = ['system', 'dark', 'light'];
  const next = order[(order.indexOf(settings.theme) + 1) % order.length]!;
  setTheme(next);
}

export function applyTheme(): void {
  const root = document.documentElement;
  if (settings.theme === 'system') {
    root.removeAttribute('data-theme');
  } else {
    root.setAttribute('data-theme', settings.theme);
  }
}

const NSFW_MODES: NsfwUiMode[] = ['hide', 'blur', 'show'];

export function setNsfwMode(mode: NsfwUiMode): void {
  settings.nsfwMode = mode;
  saveJSON('nsfwMode', mode);
  // Touching the Hide/Blur/Show control is an ambient visibility choice, distinct from (and
  // cancels) the explicit "NSFW" builtin collection's server-side nsfw=only filter.
  viewState.nsfwOnly = false;
  void runSearch(true);
}

export function cycleNsfwMode(): void {
  const next = NSFW_MODES[(NSFW_MODES.indexOf(settings.nsfwMode) + 1) % NSFW_MODES.length]!;
  setNsfwMode(next);
}

export function setColumnMinWidth(px: number): void {
  settings.columnMinWidth = Math.min(420, Math.max(120, px));
  saveJSON('columnMinWidth', settings.columnMinWidth);
}

export function bumpColumnSize(delta: number): void {
  setColumnMinWidth(settings.columnMinWidth + delta);
}

export function setSidebarOpen(open: boolean): void {
  settings.sidebarOpen = open;
  saveJSON('sidebarOpen', open);
}

export function toggleSidebar(): void {
  setSidebarOpen(!settings.sidebarOpen);
}

export function setShowTitles(show: boolean): void {
  settings.showTitles = show;
  saveJSON('showTitles', show);
}

// ---------------------------------------------------------------------------
// Toasts
// ---------------------------------------------------------------------------

export interface Toast {
  id: number;
  message: string;
  kind: 'error' | 'info' | 'success';
}

export const toastState = $state<{ items: Toast[] }>({ items: [] });
let toastSeq = 0;

export function pushToast(message: string, kind: Toast['kind'] = 'info', timeoutMs = 3500): void {
  const id = ++toastSeq;
  toastState.items.push({ id, message, kind });
  if (timeoutMs > 0) {
    setTimeout(() => dismissToast(id), timeoutMs);
  }
}

export function dismissToast(id: number): void {
  const i = toastState.items.findIndex((t) => t.id === id);
  if (i !== -1) toastState.items.splice(i, 1);
}

setApiErrorHandler((message) => pushToast(message, 'error'));

// ---------------------------------------------------------------------------
// Search / filters / sort
// ---------------------------------------------------------------------------

export interface SearchState {
  q: string;
  type: MediaType[];
  category: string[];
  tag: string[];
  favorite: boolean;
  sort: SortKey;
  seed: number;
}

function defaultSort(): SortKey {
  return 'newest';
}

export const search = $state<SearchState>({
  q: '',
  type: [],
  category: [],
  tag: [],
  favorite: false,
  sort: defaultSort(),
  seed: Math.floor(Math.random() * 1_000_000),
});

export const results = $state({
  items: [] as Item[],
  total: 0,
  loading: false,
  loadingMore: false,
  error: null as string | null,
  offset: 0,
  tookMs: 0,
  fuzzyUsed: false,
  // Bumped every time a fresh (reset) search replaces `items`, as opposed to infinite-scroll
  // pagination appending to it. Gallery.svelte uses this — not array-length comparisons alone —
  // to decide when it must fully re-layout the masonry grid rather than append: two different
  // result sets can coincidentally have the same (or a growing) length, which would otherwise
  // leave stale rects mixed in with new ones and produce duplicate keys.
  version: 0,
});

export function hasMore(): boolean {
  return results.items.length < results.total;
}

export function hasActiveFilters(): boolean {
  return (
    search.q.trim() !== '' ||
    search.type.length > 0 ||
    search.category.length > 0 ||
    search.tag.length > 0 ||
    search.favorite ||
    settings.nsfwMode !== 'hide' ||
    viewState.nsfwOnly
  );
}

function nsfwParam(): 'exclude' | 'include' | 'only' {
  if (viewState.nsfwOnly) return 'only';
  return settings.nsfwMode === 'hide' ? 'exclude' : 'include';
}

let searchRunId = 0;

export async function runSearch(reset: boolean): Promise<void> {
  const runId = ++searchRunId;
  const offset = reset ? 0 : results.offset;
  if (reset) {
    results.loading = true;
    results.error = null;
  } else {
    results.loadingMore = true;
  }
  try {
    const res = await api.items({
      q: search.q || undefined,
      type: search.type.length ? search.type : undefined,
      category: search.category.length ? search.category : undefined,
      tag: search.tag.length ? search.tag : undefined,
      favorite: search.favorite || undefined,
      nsfw: nsfwParam(),
      sort: search.q ? search.sort : search.sort === 'relevance' ? 'newest' : search.sort,
      seed: search.sort === 'random' ? search.seed : undefined,
      limit: PAGE_SIZE,
      offset,
    });
    if (runId !== searchRunId) return; // a newer search superseded this one
    results.items = reset ? res.items : results.items.concat(res.items);
    if (reset) results.version++;
    results.total = res.total;
    results.offset = offset + res.items.length;
    results.tookMs = res.took_ms;
    results.fuzzyUsed = res.fuzzy_used;
    updateDocumentTitle();
  } catch (err) {
    if (runId !== searchRunId) return;
    results.error = err instanceof ApiError ? err.detail : 'Failed to load items';
  } finally {
    if (runId === searchRunId) {
      results.loading = false;
      results.loadingMore = false;
    }
  }
}

export function loadMore(): void {
  if (results.loading || results.loadingMore || !hasMore()) return;
  void runSearch(false);
}

function updateDocumentTitle(): void {
  document.title = search.q.trim() ? `"${search.q.trim()}" — atomik-meme-web` : 'atomik-meme-web';
}

let searchDebounce: ReturnType<typeof setTimeout> | undefined;

export function setQuery(q: string, immediate = false): void {
  search.q = q;
  syncUrl();
  if (immediate) {
    void runSearch(true);
    return;
  }
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => void runSearch(true), 120);
}

export function toggleType(t: MediaType): void {
  const i = search.type.indexOf(t);
  if (i === -1) search.type.push(t);
  else search.type.splice(i, 1);
  syncUrl();
  void runSearch(true);
}

export function setCategory(names: string[]): void {
  search.category = names;
  syncUrl();
  void runSearch(true);
}

export function addTag(tag: string): void {
  if (!search.tag.includes(tag)) {
    search.tag.push(tag);
    syncUrl();
    void runSearch(true);
  }
}

export function removeTag(tag: string): void {
  const i = search.tag.indexOf(tag);
  if (i !== -1) {
    search.tag.splice(i, 1);
    syncUrl();
    void runSearch(true);
  }
}

export function setFavoriteOnly(fav: boolean): void {
  search.favorite = fav;
  syncUrl();
  void runSearch(true);
}

export function setSort(sort: SortKey): void {
  search.sort = sort;
  if (sort === 'random') search.seed = Math.floor(Math.random() * 1_000_000);
  syncUrl();
  void runSearch(true);
}

/** Context menu "Search similar" — filter by this item's tags. */
export function searchSimilar(item: Item): void {
  viewState.mode = 'gallery';
  viewState.activeCollectionId = null;
  viewState.nsfwOnly = false;
  search.q = '';
  search.type = [];
  search.category = [];
  search.tag = item.tags.slice(0, 5);
  search.favorite = false;
  syncUrl();
  void runSearch(true);
}

export function clearFilters(): void {
  search.q = '';
  search.type = [];
  search.category = [];
  search.tag = [];
  search.favorite = false;
  search.sort = defaultSort();
  setNsfwModeQuiet('hide');
  viewState.nsfwOnly = false;
  viewState.activeCollectionId = null;
  syncUrl();
  void runSearch(true);
}

function setNsfwModeQuiet(mode: NsfwUiMode): void {
  settings.nsfwMode = mode;
  saveJSON('nsfwMode', mode);
}

// ---------------------------------------------------------------------------
// URL sync (?q=&type=&cat=&tag=&sort=&fav=&nsfw=)
// ---------------------------------------------------------------------------

export function syncUrl(): void {
  const params = new URLSearchParams();
  if (search.q) params.set('q', search.q);
  if (search.type.length) params.set('type', search.type.join(','));
  if (search.category.length) params.set('cat', search.category.join(','));
  if (search.tag.length) params.set('tag', search.tag.join(','));
  if (search.sort !== defaultSort()) params.set('sort', search.sort);
  if (search.favorite) params.set('fav', '1');
  if (settings.nsfwMode !== 'hide') params.set('nsfw', settings.nsfwMode);
  const qs = params.toString();
  const url = qs ? `?${qs}` : window.location.pathname;
  window.history.pushState(null, '', url);
}

export function loadFromUrl(): void {
  const params = new URLSearchParams(window.location.search);
  search.q = params.get('q') ?? '';
  search.type = (params.get('type')?.split(',').filter(Boolean) as MediaType[]) ?? [];
  search.category = params.get('cat')?.split(',').filter(Boolean) ?? [];
  search.tag = params.get('tag')?.split(',').filter(Boolean) ?? [];
  search.sort = (params.get('sort') as SortKey) || defaultSort();
  search.favorite = params.get('fav') === '1';
  const nsfw = params.get('nsfw');
  if (nsfw === 'blur' || nsfw === 'show' || nsfw === 'hide') {
    settings.nsfwMode = nsfw;
  }
  updateDocumentTitle();
}

// ---------------------------------------------------------------------------
// Selection / lightbox
// ---------------------------------------------------------------------------

export const selection = $state({
  focusedId: null as number | null,
  lightboxId: null as number | null,
  infoOpen: true,
});

/** Column count of the currently rendered masonry grid; kept in sync by Gallery.svelte so
 * global up/down shortcuts can approximate "jump by column position". */
export const galleryState = $state({ columnCount: 1 });

export function focusedIndex(): number {
  if (selection.focusedId === null) return -1;
  return results.items.findIndex((it) => it.id === selection.focusedId);
}

/**
 * Move keyboard focus in the grid by `delta` positions (±1 for left/right, ±columnCount for
 * up/down). When nothing is focused yet — e.g. the very first arrow press after the app
 * loads, with the search box not focused — this jumps straight to the first item (index 0)
 * regardless of `delta`'s sign, rather than doing nothing: without a starting position "move
 * left" or "move up" have nothing to move relative to, so the sensible first step is the same
 * as Home. Any later call, once something is focused, moves relative to it as normal.
 */
export function moveFocus(delta: number): void {
  const items = results.items;
  if (items.length === 0) return;
  const current = focusedIndex();
  if (current === -1) {
    selection.focusedId = items[0]!.id;
    return;
  }
  const next = Math.min(items.length - 1, Math.max(0, current + delta));
  selection.focusedId = items[next]!.id;
}

export function focusHome(): void {
  if (results.items.length) selection.focusedId = results.items[0]!.id;
}

export function focusEnd(): void {
  if (results.items.length) selection.focusedId = results.items[results.items.length - 1]!.id;
}

export function focusedItem(): Item | null {
  return results.items.find((it) => it.id === selection.focusedId) ?? null;
}

export function openLightbox(id: number): void {
  selection.lightboxId = id;
  void api
    .recordView(id)
    .then(() => scheduleCountsRefresh())
    .catch(() => {});
}

export function closeLightbox(): void {
  selection.lightboxId = null;
}

export function lightboxItem(): Item | null {
  return results.items.find((it) => it.id === selection.lightboxId) ?? null;
}

export function lightboxIndex(): number {
  if (selection.lightboxId === null) return -1;
  return results.items.findIndex((it) => it.id === selection.lightboxId);
}

export function lightboxNext(): void {
  const i = lightboxIndex();
  if (i === -1) return;
  if (i + 1 < results.items.length) {
    openLightbox(results.items[i + 1]!.id);
  } else if (hasMore()) {
    void runSearch(false).then(() => {
      if (i + 1 < results.items.length) openLightbox(results.items[i + 1]!.id);
    });
  }
}

export function lightboxPrev(): void {
  const i = lightboxIndex();
  if (i > 0) openLightbox(results.items[i - 1]!.id);
}

export async function openRandomInLightbox(): Promise<void> {
  try {
    const item = await api.random({
      q: search.q || undefined,
      type: search.type.length ? search.type : undefined,
      category: search.category.length ? search.category : undefined,
      tag: search.tag.length ? search.tag : undefined,
      favorite: search.favorite || undefined,
      nsfw: nsfwParam(),
    });
    selection.lightboxId = item.id;
    if (!results.items.some((it) => it.id === item.id)) {
      results.items = [item, ...results.items];
      results.total = Math.max(results.total, results.items.length);
    }
    void api
      .recordView(item.id)
      .then(() => scheduleCountsRefresh())
      .catch(() => {});
  } catch {
    // toast already shown by apiFetch error handler
  }
}

// ---------------------------------------------------------------------------
// Collections
// ---------------------------------------------------------------------------

export type ViewMode = 'gallery' | 'duplicates';
export const viewState = $state<{ mode: ViewMode; activeCollectionId: string | number | null; nsfwOnly: boolean }>({
  mode: 'gallery',
  activeCollectionId: null,
  // True only while browsing the builtin "NSFW" collection: requests nsfw=only from the server,
  // independent of the ambient hide/blur/show display mode in settings.nsfwMode.
  nsfwOnly: false,
});

export function showGallery(): void {
  viewState.mode = 'gallery';
  viewState.activeCollectionId = null;
}

export function showDuplicates(): void {
  viewState.mode = 'duplicates';
  viewState.activeCollectionId = 'builtin:duplicates';
}

export const collectionsState = $state<{ items: Collection[]; loading: boolean }>({
  items: [],
  loading: false,
});

/** "Recently added": sort newest, since 7d — expressible as a
 * regular /api/items query, so this is a one-shot fetch rather than a runSearch()-driven one
 * (no need for the normal filter UI to apply here). */
export async function loadRecentAdded(): Promise<void> {
  results.loading = true;
  results.error = null;
  try {
    const since = new Date(Date.now() - 7 * 86400000).toISOString();
    const res = await api.items({ sort: 'newest', since, nsfw: nsfwParam(), limit: 100, offset: 0 });
    results.items = res.items;
    results.total = res.items.length;
    results.offset = res.items.length;
    results.tookMs = res.took_ms;
    results.fuzzyUsed = false;
    results.version++;
  } catch (err) {
    results.error = err instanceof ApiError ? err.detail : 'Failed to load items';
  } finally {
    results.loading = false;
  }
}

/** "Recently viewed" has no equivalent /api/items sort key (view time isn't one of the
 * supported sorts), so it's powered by the dedicated GET /api/recent?kind=viewed endpoint,
 * which already returns items ordered most-recently-viewed first. */
export async function loadRecentViewed(): Promise<void> {
  results.loading = true;
  results.error = null;
  try {
    const res = await api.recent('viewed', 50);
    results.items = res.items;
    results.total = res.items.length;
    results.offset = res.items.length;
    results.tookMs = 0;
    results.fuzzyUsed = false;
    results.version++;
  } catch (err) {
    results.error = err instanceof ApiError ? err.detail : 'Failed to load items';
  } finally {
    results.loading = false;
  }
}

export function applyCollection(c: Collection): void {
  viewState.activeCollectionId = c.id;
  const id = String(c.id);
  if (id === 'builtin:duplicates') {
    showDuplicates();
    return;
  }
  viewState.mode = 'gallery';
  // Only the builtin NSFW collection requests the server's nsfw=only filter; every other
  // collection goes back to the ambient hide/blur/show display mode.
  viewState.nsfwOnly = id === 'builtin:nsfw';
  search.q = '';
  search.type = [];
  search.category = [];
  search.tag = [];
  search.favorite = false;
  search.sort = defaultSort();
  syncUrl();

  // Both "recent" builtins bypass the normal search/filter flow entirely (see the loaders'
  // doc comments above for why), so they return early rather than falling into runSearch().
  if (id === 'builtin:recent-added') {
    void loadRecentAdded();
    return;
  }
  if (id === 'builtin:recent-viewed') {
    void loadRecentViewed();
    return;
  }

  if (c.builtin) {
    if (id === 'builtin:favorites') search.favorite = true;
    else if (id === 'builtin:gifs') search.type = ['gif'];
    else if (id === 'builtin:videos') search.type = ['video'];
    else if (id === 'builtin:images') search.type = ['image'];
    else if (id === 'builtin:nsfw') setNsfwModeQuiet('show');
    else if (id.startsWith('category:')) search.category = [id.slice('category:'.length)];
  } else {
    search.q = c.query ?? '';
    const f = (c.filters ?? {}) as Record<string, unknown>;
    if (Array.isArray(f.type)) search.type = f.type as MediaType[];
    if (Array.isArray(f.category)) search.category = f.category as string[];
    if (Array.isArray(f.tag)) search.tag = f.tag as string[];
    if (f.favorite) search.favorite = true;
    if (typeof f.sort === 'string') search.sort = f.sort as SortKey;
  }
  syncUrl();
  void runSearch(true);
}

export async function saveCurrentSearchAsCollection(name: string): Promise<void> {
  const filters: Record<string, unknown> = {};
  if (search.type.length) filters.type = search.type;
  if (search.category.length) filters.category = search.category;
  if (search.tag.length) filters.tag = search.tag;
  if (search.favorite) filters.favorite = true;
  if (search.sort !== defaultSort()) filters.sort = search.sort;
  try {
    await api.createCollection({ name, query: search.q, filters });
    pushToast(`Saved collection "${name}"`, 'success');
    await refreshCollections();
  } catch {
    // error toast already surfaced by apiFetch
  }
}

export async function renameCollection(c: Collection, name: string): Promise<void> {
  try {
    await api.updateCollection(c.id, { name, query: c.query, filters: c.filters, icon: c.icon ?? undefined });
    await refreshCollections();
  } catch {
    // error toast already surfaced by apiFetch
  }
}

export async function deleteCollectionById(id: string | number): Promise<void> {
  try {
    await api.deleteCollection(id);
    pushToast('Collection deleted', 'info');
    await refreshCollections();
  } catch {
    // error toast already surfaced by apiFetch
  }
}

export async function refreshCollections(): Promise<void> {
  collectionsState.loading = true;
  try {
    const res = await api.collections();
    collectionsState.items = res.collections;
  } catch {
    // toast already shown by apiFetch error handler
  } finally {
    collectionsState.loading = false;
  }
}

// ---------------------------------------------------------------------------
// Stats & categories
// ---------------------------------------------------------------------------

export const statsState = $state<{ data: StatsResponse | null }>({ data: null });

export async function refreshStats(): Promise<void> {
  try {
    statsState.data = await api.stats();
  } catch {
    // ignore; footer just stays empty
  }
}

export function categoryStats(): CategoryStat[] {
  return statsState.data?.categories ?? [];
}

// ---------------------------------------------------------------------------
// Categories (GET /api/categories) — shared so FilterBar's dropdown and Sidebar's list agree,
// and so both can be refreshed together (library switch, rescan completion) instead of each
// holding its own local copy that only reloads on remount.
// ---------------------------------------------------------------------------

export const categoriesState = $state<{ items: Category[]; loading: boolean }>({
  items: [],
  loading: false,
});

export async function refreshCategories(): Promise<void> {
  categoriesState.loading = true;
  try {
    const res = await api.categories();
    categoriesState.items = res.categories;
  } catch {
    // toast already shown by apiFetch's error handler
  } finally {
    categoriesState.loading = false;
  }
}

// ---------------------------------------------------------------------------
// Duplicates (GET /api/duplicates) — shared so DuplicatesView doesn't hold the only copy;
// refreshed alongside categories/collections whenever the library's contents change.
// ---------------------------------------------------------------------------

export const duplicatesState = $state<{ data: DuplicatesResponse | null; loading: boolean; error: string | null }>({
  data: null,
  loading: false,
  error: null,
});

export async function refreshDuplicates(): Promise<void> {
  duplicatesState.loading = true;
  duplicatesState.error = null;
  try {
    duplicatesState.data = await api.duplicates(true, 6);
  } catch (err) {
    duplicatesState.error = err instanceof ApiError ? err.detail : 'Failed to load duplicates';
  } finally {
    duplicatesState.loading = false;
  }
}

// Debounced re-sync of the numbers that live outside `results` and so don't naturally update
// on their own: /api/stats (StatsBar, and the sidebar's Images/GIFs/Videos/NSFW counts if a
// caller reads them from here) AND /api/collections (the sidebar's own per-builtin `count`,
// e.g. Favorites / Recently viewed). Debounced so rapid-fire favorite toggles or lightbox
// next/prev browsing (each recording a view) don't fire a refetch per keystroke.
let countsRefreshTimer: ReturnType<typeof setTimeout> | undefined;
export function scheduleCountsRefresh(delay = 300): void {
  clearTimeout(countsRefreshTimer);
  countsRefreshTimer = setTimeout(() => {
    void refreshStats();
    void refreshCollections();
  }, delay);
}

// ---------------------------------------------------------------------------
// Scan status polling
// ---------------------------------------------------------------------------

export const scanState = $state<{ running: boolean; progress: ScanProgress | null; last: Scan | null }>({
  running: false,
  progress: null,
  last: null,
});

let scanPollHandle: ReturnType<typeof setInterval> | undefined;

export async function refreshScanStatus(): Promise<void> {
  try {
    const res = await api.scanStatus();
    const wasRunning = scanState.running;
    scanState.running = res.running;
    scanState.progress = res.progress;
    scanState.last = res.last;
    if (res.running) {
      startScanPolling();
    } else {
      stopScanPolling();
      if (wasRunning) {
        // A rescan just finished in the background (independently of the library-switch flow,
        // which already refreshes these itself in finishSwitch) — the library's contents may
        // have changed, so re-sync everything derived from them rather than leaving stale
        // categories/duplicates/collection counts sitting around.
        void refreshCategories();
        void refreshCollections();
        void refreshDuplicates();
        void refreshStats();
      }
    }
  } catch {
    // ignore transient failures
  }
}

export function startScanPolling(): void {
  if (scanPollHandle) return;
  scanPollHandle = setInterval(() => void refreshScanStatus(), 2000);
}

export function stopScanPolling(): void {
  if (scanPollHandle) {
    clearInterval(scanPollHandle);
    scanPollHandle = undefined;
  }
}

export async function triggerScan(): Promise<void> {
  try {
    await api.startScan();
    pushToast('Rescan started', 'info');
    scanState.running = true;
    startScanPolling();
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      pushToast('A scan is already running', 'info');
      scanState.running = true;
      startScanPolling();
    }
  }
}

// ---------------------------------------------------------------------------
// Library selection
// ---------------------------------------------------------------------------

export type LibraryPanelPhase = 'idle' | 'picking' | 'switching' | 'done' | 'error';

interface LibraryBrowseState {
  path: string | null;
  parent: string | null;
  entries: FsEntry[];
  roots: FsRoot[];
  truncated?: boolean;
  loading: boolean;
  error: string | null;
}

export const libraryState = $state<{
  info: LibraryInfo | null;
  panelOpen: boolean;
  phase: LibraryPanelPhase;
  error: string | null;
  /** Non-null while a successfully-started switch's "large folder" warning is awaiting the
   * user's acknowledgement (Continue) before we show the indexing progress view. */
  warning: string | null;
  pendingSwitch: { path: string; libraryRoot: string } | null;
  /** The library root being switched to — set whenever phase becomes 'switching', regardless
   * of whether a warning was involved, so the progress view always has something to show. */
  switchingTo: string | null;
  progress: ScanProgress | null;
  browse: LibraryBrowseState;
}>({
  info: null,
  panelOpen: false,
  phase: 'idle',
  error: null,
  warning: null,
  pendingSwitch: null,
  switchingTo: null,
  progress: null,
  browse: { path: null, parent: null, entries: [], roots: [], loading: false, error: null },
});

export function isNoLibrary(): boolean {
  return libraryState.info !== null && libraryState.info.library_root === null;
}

export function libraryBasename(path: string): string {
  const trimmed = path.replace(/[\\/]+$/, '');
  const segments = trimmed.split(/[\\/]/);
  return segments[segments.length - 1] || trimmed || path;
}

export async function refreshLibraryInfo(): Promise<void> {
  try {
    libraryState.info = await api.library();
  } catch {
    // toast already shown by apiFetch's error handler
  }
}

export function openLibraryPanel(): void {
  libraryState.panelOpen = true;
  libraryState.phase = 'idle';
  libraryState.error = null;
  void refreshLibraryInfo();
}

/** Escape doesn't close the panel while a switch's indexing scan is in progress, so the
 * user can't accidentally lose track of it — it finishes and closes itself. */
export function closeLibraryPanel(): void {
  if (libraryState.phase === 'switching') return;
  libraryState.panelOpen = false;
  libraryState.phase = 'idle';
  libraryState.error = null;
  libraryState.warning = null;
  libraryState.pendingSwitch = null;
  libraryState.switchingTo = null;
}

export async function browseFs(path?: string): Promise<void> {
  libraryState.browse.loading = true;
  libraryState.browse.error = null;
  try {
    const res = await api.fsList(path);
    libraryState.browse = { ...res, loading: false, error: null };
  } catch (err) {
    libraryState.browse.loading = false;
    libraryState.browse.error = err instanceof ApiError ? err.detail : 'Could not list folders';
  }
}

export async function pickLibraryFolder(): Promise<void> {
  if (libraryState.phase === 'picking' || libraryState.phase === 'switching') return;
  libraryState.phase = 'picking';
  libraryState.error = null;
  try {
    const picked = await api.pickLibrary(); // undefined = user cancelled (204)
    libraryState.phase = 'idle';
    if (!picked) return;
    await beginSwitch(picked.path);
  } catch (err) {
    libraryState.phase = 'error';
    if (err instanceof ApiError && err.status === 501) {
      libraryState.error = 'Native dialog unavailable — use Browse or type a path.';
      if (libraryState.info) libraryState.info = { ...libraryState.info, native_picker: false };
    } else if (err instanceof ApiError && err.status === 409) {
      libraryState.error = 'A folder picker is already open.';
    } else {
      libraryState.error = err instanceof ApiError ? err.detail : 'Could not open the folder picker.';
    }
  }
}

/** Submits a path (typed, browsed, picked, or a recent entry) to POST /api/library. The
 * backend switches (and starts scanning) synchronously with this call — there's no separate
 * "confirm" step server-side — so a `warning` in the response just pauses OUR progress view
 * behind a Continue button; it doesn't undo anything already started. */
export async function beginSwitch(path: string): Promise<void> {
  libraryState.error = null;
  libraryState.warning = null;
  libraryState.pendingSwitch = null;
  try {
    const res = await api.switchLibrary(path);
    if (res.warning) {
      libraryState.warning = res.warning;
      libraryState.pendingSwitch = { path, libraryRoot: res.library_root };
      if (!res.scan_started) await finishSwitch(res.library_root);
      return;
    }
    if (!res.scan_started) {
      await finishSwitch(res.library_root);
      return;
    }
    libraryState.phase = 'switching';
    libraryState.switchingTo = res.library_root;
    libraryState.progress = null;
    await watchSwitchScan(res.library_root);
  } catch (err) {
    libraryState.phase = 'error';
    libraryState.error = err instanceof ApiError ? err.detail : 'Could not switch library';
  }
}

/** User clicked Continue past the "large folder" warning: start watching the (already
 * running) scan and showing progress. */
export async function confirmSwitchWarning(): Promise<void> {
  const pending = libraryState.pendingSwitch;
  if (!pending) return;
  libraryState.warning = null;
  libraryState.phase = 'switching';
  libraryState.switchingTo = pending.libraryRoot;
  libraryState.progress = null;
  await watchSwitchScan(pending.libraryRoot);
}

export function dismissSwitchWarning(): void {
  const pending = libraryState.pendingSwitch;
  libraryState.warning = null;
  libraryState.pendingSwitch = null;
  libraryState.phase = 'idle';
  if (pending) {
    // The backend already switched (and started scanning) as part of the original request —
    // there's no undo. Dismissing here only means "stop showing me the progress view", so at
    // least resync the chip/panel with the library that's now actually active, and let the
    // existing TopBar rescan indicator pick up the still-running scan.
    void refreshLibraryInfo();
    void refreshScanStatus();
  }
}

let switchPollHandle: ReturnType<typeof setInterval> | undefined;

async function checkSwitchScan(): Promise<boolean> {
  try {
    const status = await api.scanStatus();
    scanState.running = status.running;
    scanState.progress = status.progress;
    scanState.last = status.last;
    libraryState.progress = status.progress;
    return status.running;
  } catch {
    return true; // transient poll failure: keep trying rather than getting stuck
  }
}

async function watchSwitchScan(libraryRoot: string): Promise<void> {
  clearInterval(switchPollHandle);
  switchPollHandle = undefined;
  const stillRunning = await checkSwitchScan();
  if (!stillRunning) {
    await finishSwitch(libraryRoot);
    return;
  }
  switchPollHandle = setInterval(() => {
    void checkSwitchScan().then((running) => {
      if (!running) {
        clearInterval(switchPollHandle);
        switchPollHandle = undefined;
        void finishSwitch(libraryRoot);
      }
    });
  }, 1000);
}

async function finishSwitch(libraryRoot: string): Promise<void> {
  libraryState.phase = 'done';
  // Reset search/filters/URL for the new library.
  search.q = '';
  search.type = [];
  search.category = [];
  search.tag = [];
  search.favorite = false;
  search.sort = defaultSort();
  viewState.mode = 'gallery';
  viewState.activeCollectionId = null;
  viewState.nsfwOnly = false;
  window.history.pushState(null, '', window.location.pathname);
  await Promise.all([
    refreshLibraryInfo(),
    refreshStats(),
    refreshCollections(),
    refreshCategories(),
    refreshDuplicates(),
    runSearch(true),
  ]);
  // Prefer the freshly-refreshed library-wide total over the current (possibly NSFW-filtered
  // or otherwise narrowed) search result count, so e.g. "43 items" doesn't read as "39" just
  // because NSFW is hidden by default.
  const total = statsState.data?.total ?? results.total;
  pushToast(`Library switched to ${libraryBasename(libraryRoot)} — ${total.toLocaleString()} items`, 'success');
  libraryState.pendingSwitch = null;
  libraryState.warning = null;
  libraryState.switchingTo = null;
  closeLibraryPanel();
}

export async function switchToRecent(path: string): Promise<void> {
  await beginSwitch(path);
}

export async function removeRecentLibrary(path: string): Promise<void> {
  try {
    await api.removeRecentLibrary(path);
    await refreshLibraryInfo();
  } catch {
    // toast already shown by apiFetch's error handler
  }
}

export async function revealLibrary(): Promise<void> {
  try {
    await api.revealLibrary();
  } catch {
    // toast already shown by apiFetch's error handler
  }
}
