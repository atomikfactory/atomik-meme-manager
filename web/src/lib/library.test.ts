// Covers the library-panel state machine:
// idle -> picking -> switching -> done/error, plus the warning-before-switching detour and
// the Ctrl+O shortcut (see shortcuts.test.ts for the latter).
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      library: vi.fn(),
      switchLibrary: vi.fn(),
      pickLibrary: vi.fn(),
      scanStatus: vi.fn(),
      stats: vi.fn(),
      collections: vi.fn(),
      categories: vi.fn(),
      duplicates: vi.fn(),
      items: vi.fn(),
      fsList: vi.fn(),
      removeRecentLibrary: vi.fn(),
    },
  };
});

import { api, ApiError } from './api';
import {
  beginSwitch,
  browseFs,
  categoriesState,
  closeLibraryPanel,
  confirmSwitchWarning,
  duplicatesState,
  libraryState,
  openLibraryPanel,
  pickLibraryFolder,
  refreshScanStatus,
  removeRecentLibrary,
  results,
  scanState,
  statsState,
  stopScanPolling,
  toastState,
} from './state.svelte';

const apiMock = vi.mocked(api, true);

function progress(scanned: number, total: number) {
  return { scanned, total, added: 0, modified: 0, removed: 0, moved: 0 };
}

function emptyStats() {
  return {
    total: 0,
    by_type: { image: 0, gif: 0, video: 0 },
    favorites: 0,
    total_bytes: 0,
    nsfw: 0,
    categories: [],
    last_scan: null,
    scanning: false,
  };
}

function searchResponse(total: number) {
  return { items: [], total, limit: 100, offset: 0, took_ms: 1, fuzzy_used: false };
}

beforeEach(() => {
  vi.clearAllMocks();
  toastState.items.length = 0;
  libraryState.info = null;
  libraryState.panelOpen = false;
  libraryState.phase = 'idle';
  libraryState.error = null;
  libraryState.warning = null;
  libraryState.pendingSwitch = null;
  libraryState.switchingTo = null;
  libraryState.progress = null;
  libraryState.browse = { path: null, parent: null, entries: [], roots: [], loading: false, error: null };
  statsState.data = null;
  categoriesState.items = [];
  duplicatesState.data = null;
  scanState.running = false;
  scanState.progress = null;

  apiMock.library.mockResolvedValue({
    library_root: 'D:\\lib',
    data_dir: 'D:\\lib\\.meme-web',
    recent: [],
    native_picker: true,
    remember: true,
  });
  apiMock.stats.mockResolvedValue(emptyStats());
  apiMock.collections.mockResolvedValue({ collections: [] });
  apiMock.categories.mockResolvedValue({ categories: [] });
  apiMock.duplicates.mockResolvedValue({ groups: [], computed_at: '2026-01-01T00:00:00Z' });
  apiMock.items.mockResolvedValue(searchResponse(0));
});

afterEach(() => {
  vi.useRealTimers();
  stopScanPolling(); // refreshScanStatus(running: true) starts a real 2s interval; don't leak it
});

describe('library panel state machine: idle -> picking -> switching -> done/error', () => {
  it('openLibraryPanel opens idle and fetches library info', async () => {
    openLibraryPanel();
    expect(libraryState.panelOpen).toBe(true);
    expect(libraryState.phase).toBe('idle');
    await Promise.resolve();
    expect(apiMock.library).toHaveBeenCalledTimes(1);
  });

  it('picking a folder goes idle -> picking, then a scan_started switch lands in switching', async () => {
    apiMock.pickLibrary.mockResolvedValue({ path: 'D:\\Media\\memes-2024' });
    apiMock.switchLibrary.mockResolvedValue({ library_root: 'D:\\Media\\memes-2024', data_dir: 'x', scan_started: true });
    apiMock.scanStatus.mockResolvedValue({ running: true, progress: progress(3, 10), last: null });

    const promise = pickLibraryFolder();
    expect(libraryState.phase).toBe('picking');
    await promise;

    expect(apiMock.switchLibrary).toHaveBeenCalledWith('D:\\Media\\memes-2024');
    expect(libraryState.phase).toBe('switching');
    expect(libraryState.switchingTo).toBe('D:\\Media\\memes-2024');
    expect(libraryState.progress).toEqual(progress(3, 10));
  });

  it('a 204 (cancelled) pick returns to idle without switching', async () => {
    apiMock.pickLibrary.mockResolvedValue(undefined);
    await pickLibraryFolder();
    expect(libraryState.phase).toBe('idle');
    expect(apiMock.switchLibrary).not.toHaveBeenCalled();
  });

  it('a 501 from the picker moves to error and flags native_picker false', async () => {
    libraryState.info = { library_root: 'D:\\lib', data_dir: 'x', recent: [], native_picker: true, remember: true };
    apiMock.pickLibrary.mockRejectedValue(new ApiError(501, 'Tk unavailable'));
    await pickLibraryFolder();
    expect(libraryState.phase).toBe('error');
    expect(libraryState.info?.native_picker).toBe(false);
    expect(libraryState.error).toContain('Native dialog unavailable');
  });

  it('a 409 from the picker (already open) moves to error with a friendly message', async () => {
    apiMock.pickLibrary.mockRejectedValue(new ApiError(409, 'A folder picker is already open'));
    await pickLibraryFolder();
    expect(libraryState.phase).toBe('error');
    expect(libraryState.error).toContain('already open');
  });

  it('picking/switching guards against re-entry', async () => {
    libraryState.phase = 'switching';
    await pickLibraryFolder();
    expect(apiMock.pickLibrary).not.toHaveBeenCalled();
  });

  it('a same-path switch (scan_started: false) resolves straight to done, no switching phase', async () => {
    apiMock.switchLibrary.mockResolvedValue({ library_root: 'D:\\lib', data_dir: 'x', scan_started: false });
    apiMock.stats.mockResolvedValue({ ...emptyStats(), total: 5 });
    apiMock.items.mockResolvedValue(searchResponse(5));

    await beginSwitch('D:\\lib');

    // 'done' is momentary — finishSwitch immediately closes the panel afterwards, so the
    // settled, observable end state is "closed", not a lingering phase==='done'.
    expect(libraryState.panelOpen).toBe(false);
    expect(apiMock.scanStatus).not.toHaveBeenCalled();
    expect(results.total).toBe(5);
    const toast = toastState.items.at(-1);
    expect(toast?.message).toBe('Library switched to lib — 5 items');
  });

  it('the toast reports the library-wide total from /api/stats, not the (possibly NSFW-filtered) search result count', async () => {
    // Regression: 43 real items in the library, but only 39 come back from the default
    // NSFW-hidden search — the toast must say 43, not 39.
    apiMock.switchLibrary.mockResolvedValue({ library_root: 'D:\\lib', data_dir: 'x', scan_started: false });
    apiMock.stats.mockResolvedValue({ ...emptyStats(), total: 43 });
    apiMock.items.mockResolvedValue(searchResponse(39));

    await beginSwitch('D:\\lib');

    expect(results.total).toBe(39);
    const toast = toastState.items.at(-1);
    expect(toast?.message).toBe('Library switched to lib — 43 items');
  });

  it('falls back to the search result total if /api/stats fails', async () => {
    apiMock.switchLibrary.mockResolvedValue({ library_root: 'D:\\lib', data_dir: 'x', scan_started: false });
    apiMock.stats.mockRejectedValue(new ApiError(500, 'stats unavailable'));
    apiMock.items.mockResolvedValue(searchResponse(7));

    await beginSwitch('D:\\lib');

    const toast = toastState.items.at(-1);
    expect(toast?.message).toBe('Library switched to lib — 7 items');
  });

  it('a switch with a warning pauses before switching until Continue is clicked', async () => {
    apiMock.switchLibrary.mockResolvedValue({
      library_root: 'D:\\big',
      data_dir: 'x',
      scan_started: true,
      warning: 'large folder',
    });
    await beginSwitch('D:\\big');

    expect(libraryState.warning).toBe('large folder');
    expect(libraryState.phase).not.toBe('switching');
    expect(apiMock.scanStatus).not.toHaveBeenCalled();

    apiMock.scanStatus.mockResolvedValue({ running: false, progress: progress(4, 4), last: null });
    await confirmSwitchWarning();

    // The first poll already reports not-running, so the switch finishes (and the panel
    // closes) without ever showing a lingering "switching" progress view.
    expect(libraryState.warning).toBeNull();
    expect(libraryState.panelOpen).toBe(false);
  });

  it('an invalid path surfaces the server {detail} and moves to error', async () => {
    apiMock.switchLibrary.mockRejectedValue(new ApiError(404, 'Path does not exist or is not a directory'));
    await beginSwitch('Z:\\nope');
    expect(libraryState.phase).toBe('error');
    expect(libraryState.error).toBe('Path does not exist or is not a directory');
  });

  it('resets search/filters and refreshes everything once the post-switch scan finishes, including categories and duplicates', async () => {
    apiMock.switchLibrary.mockResolvedValue({ library_root: 'D:\\lib', data_dir: 'x', scan_started: true });
    apiMock.scanStatus.mockResolvedValue({ running: false, progress: progress(12, 12), last: null });
    apiMock.stats.mockResolvedValue({ ...emptyStats(), total: 12 });
    apiMock.items.mockResolvedValue(searchResponse(12));
    apiMock.categories.mockResolvedValue({
      categories: [{ id: 1, name: 'new-library-only', folder: '01-new', count: 3, pinned: false }],
    });
    apiMock.duplicates.mockResolvedValue({
      groups: [{ kind: 'exact', distance: 0, items: [] }],
      computed_at: '2026-02-02T00:00:00Z',
    });

    await beginSwitch('D:\\lib');

    expect(apiMock.stats).toHaveBeenCalled();
    expect(apiMock.collections).toHaveBeenCalled();
    expect(apiMock.items).toHaveBeenCalled();
    expect(results.total).toBe(12);
    expect(libraryState.panelOpen).toBe(false); // finishSwitch closes the panel

    // The bug this regresses: FilterBar's/Sidebar's category list (and the duplicates view)
    // used to keep whatever the *previous* library had until something happened to remount
    // them. finishSwitch must now pull fresh copies into the shared stores directly.
    expect(apiMock.categories).toHaveBeenCalledTimes(1);
    expect(categoriesState.items).toEqual([{ id: 1, name: 'new-library-only', folder: '01-new', count: 3, pinned: false }]);
    expect(apiMock.duplicates).toHaveBeenCalledTimes(1);
    expect(duplicatesState.data?.groups).toHaveLength(1);
  });

  it('a same-path (no-op) switch also refreshes categories and duplicates', async () => {
    apiMock.switchLibrary.mockResolvedValue({ library_root: 'D:\\lib', data_dir: 'x', scan_started: false });
    apiMock.categories.mockResolvedValue({ categories: [{ id: 2, name: 'still-fresh', folder: '02-x', count: 1, pinned: false }] });

    await beginSwitch('D:\\lib');

    expect(apiMock.categories).toHaveBeenCalledTimes(1);
    expect(categoriesState.items).toEqual([{ id: 2, name: 'still-fresh', folder: '02-x', count: 1, pinned: false }]);
    expect(apiMock.duplicates).toHaveBeenCalledTimes(1);
  });

  it('keeps polling every second until the scan finishes, then finishes the switch', async () => {
    vi.useFakeTimers();
    apiMock.switchLibrary.mockResolvedValue({ library_root: 'D:\\lib', data_dir: 'x', scan_started: true });
    apiMock.scanStatus
      .mockResolvedValueOnce({ running: true, progress: progress(1, 3), last: null })
      .mockResolvedValueOnce({ running: true, progress: progress(2, 3), last: null })
      .mockResolvedValueOnce({ running: false, progress: progress(3, 3), last: null });

    const switchPromise = beginSwitch('D:\\lib');
    await vi.advanceTimersByTimeAsync(0);
    expect(libraryState.phase).toBe('switching');
    expect(apiMock.scanStatus).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1000);
    expect(apiMock.scanStatus).toHaveBeenCalledTimes(2);
    expect(libraryState.phase).toBe('switching');

    await vi.advanceTimersByTimeAsync(1000);
    expect(apiMock.scanStatus).toHaveBeenCalledTimes(3);
    expect(libraryState.panelOpen).toBe(false); // finished and closed itself

    await switchPromise;
  });

  it('closeLibraryPanel is a no-op while switching, but works once done', async () => {
    libraryState.panelOpen = true;
    libraryState.phase = 'switching';
    closeLibraryPanel();
    expect(libraryState.panelOpen).toBe(true);

    libraryState.phase = 'done';
    closeLibraryPanel();
    expect(libraryState.panelOpen).toBe(false);
    expect(libraryState.phase).toBe('idle');
  });

  it('browseFs stores the fs/list response and surfaces errors', async () => {
    apiMock.fsList.mockResolvedValueOnce({
      path: 'D:\\',
      parent: null,
      entries: [{ name: 'Media', path: 'D:\\Media', has_children: true }],
      roots: [{ name: 'D:\\', path: 'D:\\' }],
    });
    await browseFs('D:\\');
    expect(libraryState.browse.path).toBe('D:\\');
    expect(libraryState.browse.entries).toHaveLength(1);
    expect(libraryState.browse.loading).toBe(false);

    apiMock.fsList.mockRejectedValueOnce(new ApiError(403, 'Permission denied'));
    await browseFs('D:\\locked');
    expect(libraryState.browse.error).toBe('Permission denied');
  });

  it('removeRecentLibrary calls the API and refreshes library info', async () => {
    await removeRecentLibrary('D:\\gone');
    expect(apiMock.removeRecentLibrary).toHaveBeenCalledWith('D:\\gone');
    expect(apiMock.library).toHaveBeenCalled();
  });
});

describe('refreshScanStatus: refreshes derived data when a background rescan completes', () => {
  it('refreshes categories/collections/duplicates/stats on the running true -> false transition', async () => {
    scanState.running = true; // a scan (e.g. from the TopBar Rescan button) is already in flight
    apiMock.scanStatus.mockResolvedValue({ running: false, progress: progress(20, 20), last: null });

    await refreshScanStatus();

    expect(scanState.running).toBe(false);
    expect(apiMock.categories).toHaveBeenCalledTimes(1);
    expect(apiMock.collections).toHaveBeenCalledTimes(1);
    expect(apiMock.duplicates).toHaveBeenCalledTimes(1);
    expect(apiMock.stats).toHaveBeenCalledTimes(1);
  });

  it('does nothing extra while a scan is still running, or when it was already idle', async () => {
    scanState.running = false;
    apiMock.scanStatus.mockResolvedValue({ running: false, progress: null, last: null });
    await refreshScanStatus();
    expect(apiMock.categories).not.toHaveBeenCalled();

    scanState.running = true;
    apiMock.scanStatus.mockResolvedValue({ running: true, progress: progress(5, 20), last: null });
    await refreshScanStatus();
    expect(apiMock.categories).not.toHaveBeenCalled();
  });
});
