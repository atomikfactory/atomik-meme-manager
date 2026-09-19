// Typed client for every endpoint of the atomik-meme-web API.
import type {
  ApiErrorBody,
  CategoriesResponse,
  CollectionsResponse,
  Collection,
  ConfigResponse,
  DuplicatesResponse,
  FavoriteResponse,
  FsListResponse,
  HealthResponse,
  InboxResponse,
  Item,
  ItemDetail,
  LibraryInfo,
  LibraryPickResponse,
  LibrarySwitchResponse,
  OkResponse,
  RecentResponse,
  ScanStartResponse,
  ScanStatusResponse,
  SearchParams,
  SearchResponse,
  StatsResponse,
  SuggestResponse,
  TagsResponse,
  ViewResponse,
} from './types';

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

/** onError is called with a human string any time a request fails; wire it to the toast store. */
let errorHandler: ((message: string) => void) | null = null;
export function setApiErrorHandler(handler: (message: string) => void): void {
  errorHandler = handler;
}

// The backend requires this header on every request as CSRF protection (a cross-site page
// can make the browser send a "simple" cross-origin request, but can't set a custom header
// without triggering a CORS preflight it would fail). Sent on GET too, per the backend's
// instructions — harmless, and one code path instead of two.
const CSRF_HEADER_NAME = 'X-Atomik-Meme';
const CSRF_HEADER_VALUE = '1';

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set(CSRF_HEADER_NAME, CSRF_HEADER_VALUE);
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers });
  } catch (err) {
    const message = 'Network error: could not reach the server';
    errorHandler?.(message);
    throw new ApiError(0, message);
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = (await res.json()) as ApiErrorBody;
      if (body?.detail) detail = body.detail;
    } catch {
      // ignore parse errors, keep default detail
    }
    // 403 here almost always means the CSRF header above got stripped or rejected somehow
    // (we always send it) — call that out distinctly so it doesn't read like an ordinary
    // not-found/validation error.
    errorHandler?.(res.status === 403 ? `Blocked (403): ${detail}` : detail);
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    search.set(key, String(value));
  }
  const s = search.toString();
  return s ? `?${s}` : '';
}

export function searchParamsToQuery(params: SearchParams): string {
  return qs({
    q: params.q,
    type: params.type?.join(','),
    category: params.category?.join(','),
    tag: params.tag?.join(','),
    favorite: params.favorite ? true : undefined,
    nsfw: params.nsfw,
    sort: params.sort,
    seed: params.seed,
    limit: params.limit,
    offset: params.offset,
    min_duration: params.min_duration,
    max_duration: params.max_duration,
    since: params.since,
  });
}

export const api = {
  health(): Promise<HealthResponse> {
    return apiFetch('/api/health');
  },
  config(): Promise<ConfigResponse> {
    return apiFetch('/api/config');
  },
  stats(): Promise<StatsResponse> {
    return apiFetch('/api/stats');
  },
  items(params: SearchParams): Promise<SearchResponse> {
    return apiFetch(`/api/items${searchParamsToQuery(params)}`);
  },
  item(id: number): Promise<ItemDetail> {
    return apiFetch(`/api/items/${id}`);
  },
  thumbUrl(id: number, w = 320): string {
    return `/api/items/${id}/thumb?w=${w}`;
  },
  mediaUrl(id: number): string {
    return `/api/items/${id}/media`;
  },
  recordView(id: number): Promise<ViewResponse> {
    return apiFetch(`/api/items/${id}/view`, { method: 'POST' });
  },
  setFavorite(id: number, favorite: boolean): Promise<FavoriteResponse> {
    return apiFetch(`/api/items/${id}/favorite`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ favorite }),
    });
  },
  openOriginal(id: number): Promise<OkResponse> {
    return apiFetch(`/api/items/${id}/open`, { method: 'POST' });
  },
  reveal(id: number): Promise<OkResponse> {
    return apiFetch(`/api/items/${id}/reveal`, { method: 'POST' });
  },
  random(params: SearchParams): Promise<Item> {
    return apiFetch(`/api/random${searchParamsToQuery(params)}`);
  },
  recent(kind: 'added' | 'viewed', limit = 50): Promise<RecentResponse> {
    return apiFetch(`/api/recent${qs({ kind, limit })}`);
  },
  tags(q?: string, limit = 50): Promise<TagsResponse> {
    return apiFetch(`/api/tags${qs({ q, limit })}`);
  },
  categories(): Promise<CategoriesResponse> {
    return apiFetch('/api/categories');
  },
  suggest(q: string, limit = 8): Promise<SuggestResponse> {
    return apiFetch(`/api/suggest${qs({ q, limit })}`);
  },
  duplicates(near = true, maxDistance = 6): Promise<DuplicatesResponse> {
    return apiFetch(`/api/duplicates${qs({ near, max_distance: maxDistance })}`);
  },
  collections(): Promise<CollectionsResponse> {
    return apiFetch('/api/collections');
  },
  createCollection(input: {
    name: string;
    query: string;
    filters: Record<string, unknown>;
    icon?: string;
  }): Promise<Collection> {
    return apiFetch('/api/collections', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    });
  },
  updateCollection(
    id: string | number,
    input: { name: string; query: string; filters: Record<string, unknown>; icon?: string }
  ): Promise<Collection> {
    return apiFetch(`/api/collections/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    });
  },
  deleteCollection(id: string | number): Promise<void> {
    return apiFetch(`/api/collections/${id}`, { method: 'DELETE' });
  },
  scanStatus(): Promise<ScanStatusResponse> {
    return apiFetch('/api/scan/status');
  },
  startScan(): Promise<ScanStartResponse> {
    return apiFetch('/api/scan', { method: 'POST' });
  },
  uploadInbox(files: File[]): Promise<InboxResponse> {
    const form = new FormData();
    for (const f of files) form.append('files', f);
    return apiFetch('/api/inbox', { method: 'POST', body: form });
  },

  // --- Library selection -------------------------------
  library(): Promise<LibraryInfo> {
    return apiFetch('/api/library');
  },
  switchLibrary(path: string): Promise<LibrarySwitchResponse> {
    return apiFetch('/api/library', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
  },
  /** POST /api/library/pick. Resolves to the chosen path, or undefined when the user
   * cancelled (204 No Content). 501/409 surface as a thrown ApiError. */
  pickLibrary(): Promise<LibraryPickResponse | undefined> {
    return apiFetch('/api/library/pick', { method: 'POST' });
  },
  revealLibrary(): Promise<OkResponse> {
    return apiFetch('/api/library/reveal', { method: 'POST' });
  },
  removeRecentLibrary(path: string): Promise<void> {
    return apiFetch(`/api/library/recent${qs({ path })}`, { method: 'DELETE' });
  },
  fsList(path?: string): Promise<FsListResponse> {
    return apiFetch(`/api/fs/list${qs({ path })}`);
  },
};
