// Types mirroring the atomik-meme-web API contract exactly (field names included).

export type MediaType = 'image' | 'gif' | 'video';
export type NsfwMode = 'exclude' | 'include' | 'only';
export type NsfwUiMode = 'hide' | 'blur' | 'show';
export type SortKey = 'relevance' | 'newest' | 'oldest' | 'name' | 'size' | 'duration' | 'random';

export interface ItemCategory {
  id: number;
  name: string;
  folder: string;
}

export interface Item {
  id: number;
  path: string;
  rel_path: string;
  name: string;
  stem: string;
  ext: string;
  media_type: MediaType;
  size_bytes: number;
  width: number | null;
  height: number | null;
  aspect_ratio: number | null;
  duration_s: number | null;
  fps: number | null;
  created_at: string;
  modified_at: string;
  indexed_at: string;
  sha256: string;
  dhash: string | null;
  title: string | null;
  description: string | null;
  ocr_text: string | null;
  tags: string[];
  topics: string[];
  tone: string[];
  meme_type: string | null;
  template: string | null;
  category: ItemCategory | null;
  nsfw: boolean;
  confidence: number | null;
  favorite: boolean;
  view_count: number;
  last_viewed_at: string | null;
  thumb_url: string;
  media_url: string;
}

export interface ItemDetail extends Item {
  sidecar: Record<string, unknown> | null;
  duplicates: Item[];
  neighbors?: { prev_id: number | null; next_id: number | null };
}

export interface SearchResponse {
  items: Item[];
  total: number;
  limit: number;
  offset: number;
  took_ms: number;
  fuzzy_used: boolean;
}

export interface SearchParams {
  q?: string;
  type?: MediaType[];
  category?: string[];
  tag?: string[];
  favorite?: boolean;
  nsfw?: NsfwMode;
  sort?: SortKey;
  seed?: number;
  limit?: number;
  offset?: number;
  min_duration?: number;
  max_duration?: number;
  since?: string;
}

export interface HealthResponse {
  status: string;
  version: string;
  library_root: string | null;
  data_dir: string;
  ffmpeg: boolean;
  items: number;
}

export interface ConfigResponse {
  inbox_enabled: boolean;
  nsfw_default: NsfwUiMode;
  thumb_sizes: number[];
  library_root: string | null;
}

export interface CategoryStat {
  name: string;
  folder: string;
  count: number;
}

export interface Scan {
  id: number;
  started_at: string;
  finished_at: string | null;
  total: number | null;
  added: number | null;
  modified: number | null;
  removed: number | null;
  moved: number | null;
  duration_s: number | null;
  error: string | null;
}

export interface StatsResponse {
  total: number;
  by_type: { image: number; gif: number; video: number };
  favorites: number;
  total_bytes: number;
  nsfw: number;
  categories: CategoryStat[];
  last_scan: Scan | null;
  scanning: boolean;
}

export interface RecentResponse {
  items: Item[];
}

export interface Tag {
  tag: string;
  count: number;
}

export interface TagsResponse {
  tags: Tag[];
}

export interface Category {
  id: number;
  name: string;
  folder: string;
  count: number;
  pinned: boolean;
}

export interface CategoriesResponse {
  categories: Category[];
}

export type SuggestionKind = 'title' | 'tag' | 'file' | 'category';

export interface Suggestion {
  kind: SuggestionKind;
  text: string;
  item_id?: number;
}

export interface SuggestResponse {
  suggestions: Suggestion[];
}

export interface DuplicateGroup {
  kind: 'exact' | 'near';
  distance: number;
  items: Item[];
}

export interface DuplicatesResponse {
  groups: DuplicateGroup[];
  computed_at: string;
}

export interface Collection {
  id: string | number;
  name: string;
  query: string;
  filters: Record<string, unknown>;
  icon: string | null;
  builtin: boolean;
  created_at?: string;
  updated_at?: string;
  count?: number;
}

export interface CollectionsResponse {
  collections: Collection[];
}

export interface ScanProgress {
  scanned: number;
  total: number;
  added: number;
  modified: number;
  removed: number;
  moved: number;
}

export interface ScanStatusResponse {
  running: boolean;
  progress: ScanProgress | null;
  last: Scan | null;
}

export interface ScanStartResponse {
  started?: boolean;
  running?: boolean;
}

export interface FavoriteResponse {
  favorite: boolean;
}

export interface ViewResponse {
  view_count: number;
  last_viewed_at: string;
}

export interface OkResponse {
  ok: true;
}

export interface InboxResponse {
  saved: string[];
  inbox_dir: string;
}

export interface ApiErrorBody {
  detail: string;
}

// --- Library selection -------------------------------------

export interface RecentLibrary {
  path: string;
  last_opened: string;
  exists: boolean;
}

export interface LibraryInfo {
  library_root: string | null;
  data_dir: string;
  recent: RecentLibrary[];
  native_picker: boolean;
  remember: boolean;
}

export interface LibrarySwitchResponse {
  library_root: string;
  data_dir: string;
  scan_started: boolean;
  warning?: string;
}

export interface LibraryPickResponse {
  path: string;
}

export interface FsEntry {
  name: string;
  path: string;
  has_children: boolean;
}

export interface FsRoot {
  name: string;
  path: string;
}

export interface FsListResponse {
  path: string | null;
  parent: string | null;
  entries: FsEntry[];
  roots: FsRoot[];
  truncated?: boolean;
}
