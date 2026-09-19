#!/usr/bin/env node
// Dependency-free mock of the atomik-meme-web backend API, including the library
// selection endpoints, for frontend development without
// the Python server. Serves ~60 deterministic fixture items backed cyclically by real files
// read from D:\Projects\atomik-meme\output (read-only).
// Never writes into output/ or anywhere outside web/ (inbox uploads land in
// web/mock/inbox-uploads/, not the real repo-root input/ dir).
//
// Set MOCK_NO_LIBRARY=1 to start with no library selected (library_root: null), exercising the
// full-screen "choose your folder" empty state.

import http from 'node:http';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const HOST = '127.0.0.1';
const PORT = 8765;
const LIBRARY_ROOT = path.resolve(__dirname, '..', '..', 'output');
const INBOX_DIR = path.join(__dirname, 'inbox-uploads');
const INBOX_ENABLED = true;
const FIXTURE_COUNT = 60;
const MOCK_NO_LIBRARY = process.env.MOCK_NO_LIBRARY === '1';

// ---------------------------------------------------------------------------
// Discover real media files (read-only) to back fixtures with real bytes.
// ---------------------------------------------------------------------------

function walk(dir) {
  let out = [];
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const e of entries) {
    if (e.name.startsWith('.')) continue;
    const full = path.join(dir, e.name);
    if (e.isDirectory()) out = out.concat(walk(full));
    else if (/\.(jpe?g|gif|mp4)$/i.test(e.name)) out.push(full);
  }
  return out;
}

const REAL_FILES = walk(LIBRARY_ROOT).sort();
if (REAL_FILES.length === 0) {
  console.warn(`[atomik-meme-web mock] No media found under ${LIBRARY_ROOT}; /thumb and /media will 404.`);
} else {
  console.log(`[atomik-meme-web mock] Found ${REAL_FILES.length} real files under ${LIBRARY_ROOT}`);
}

// ---------------------------------------------------------------------------
// Deterministic fixture generation
// ---------------------------------------------------------------------------

function mulberry32(seed) {
  let a = seed >>> 0;
  return function rng() {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function pick(rng, arr) {
  return arr[Math.floor(rng() * arr.length)];
}

function sample(rng, arr, n) {
  const pool = arr.slice();
  const out = [];
  while (pool.length && out.length < n) {
    const i = Math.floor(rng() * pool.length);
    out.push(pool.splice(i, 1)[0]);
  }
  return out;
}

function toTitleCase(s) {
  return s.replace(/(^|[\s-])\w/g, (m) => m.toUpperCase());
}

const ADJECTIVES = [
  'sarcastic', 'confused', 'ancient', 'suspicious', 'wholesome', 'chaotic', 'tired',
  'overconfident', 'nostalgic', 'unhinged', 'polite', 'dramatic', 'sleepy', 'feral',
  'majestic', 'awkward', 'petty', 'blessed', 'cursed', 'unbothered',
];
const NOUNS = [
  'cat', 'intern', 'manager', 'gremlin', 'wizard', 'toddler', 'accountant', 'raccoon',
  'grandpa', 'coworker', 'developer', 'tourist', 'landlord', 'philosopher', 'robot',
  'goose', 'pirate', 'influencer', 'gnome', 'astronaut',
];
const CATEGORY_NAMES = [
  'wealth-dreams-irony', 'relationship-drama-comics', 'education-and-language-jokes',
  'office-software-humor', 'tech-evolution-satire', 'political-sarcasm-memes',
  'absurd-travel-hacks', 'anime-prank-gags', 'tv-show-fan-culture', 'others', 'it',
];
const TAG_POOL = [
  'excel', 'powerpoint', 'wifi', 'monday', 'coffee', 'deadline', 'crypto', 'landlord',
  'group-chat', 'therapy', 'gym', 'cat', 'dog', 'anime', 'sitcom', 'election', 'airport',
  'wedding', 'exam', 'zoom-call', 'printer', 'boss', 'roommate', 'weather', 'traffic',
];
const TOPIC_POOL = ['technology', 'relationships', 'money', 'work', 'travel', 'politics', 'school', 'anime', 'television'];
const TONE_POOL = ['sarcastic', 'wholesome', 'deadpan', 'absurd', 'nostalgic', 'dramatic'];
const MEME_TYPES = ['text-post', 'reaction', 'template', 'screenshot', 'comic-panel'];

function hexFlipBit(hex, bitIndex) {
  const chars = hex.split('');
  const charIndex = Math.floor(bitIndex / 4);
  const bitInNibble = bitIndex % 4;
  const nibble = parseInt(chars[charIndex], 16);
  chars[charIndex] = (nibble ^ (1 << bitInNibble)).toString(16);
  return chars.join('');
}

const now = Date.now();
const items = [];

for (let i = 0; i < FIXTURE_COUNT; i++) {
  const rng = mulberry32(1000 + i * 97);
  const sourceFile = REAL_FILES.length ? REAL_FILES[i % REAL_FILES.length] : null;
  const ext = (sourceFile ? path.extname(sourceFile) : '.jpg').toLowerCase();
  const media_type = ext === '.gif' ? 'gif' : ext === '.mp4' ? 'video' : 'image';
  const adjective = pick(rng, ADJECTIVES);
  const noun = pick(rng, NOUNS);
  const stem = `${adjective}-${noun}-${i + 1}`;
  const name = `${stem}${ext}`;
  const categoryIndex = i % CATEGORY_NAMES.length;
  const categoryName = CATEGORY_NAMES[categoryIndex];
  const category = {
    id: categoryIndex + 1,
    name: categoryName,
    folder: `${String(categoryIndex + 1).padStart(2, '0')}-${categoryName}`,
  };
  const tags = sample(rng, TAG_POOL, 2 + Math.floor(rng() * 3));
  const topics = sample(rng, TOPIC_POOL, 1 + Math.floor(rng() * 2));
  const tone = sample(rng, TONE_POOL, 1);
  const nsfw = i % 13 === 0;
  let stat;
  try {
    stat = sourceFile ? fs.statSync(sourceFile) : null;
  } catch {
    stat = null;
  }
  const size_bytes = stat ? stat.size : 40000 + Math.floor(rng() * 200000);
  const dims =
    media_type === 'video'
      ? { width: 1280, height: 720 }
      : { width: 480 + (i % 6) * 60, height: 720 - (i % 5) * 40 };
  const duration_s = media_type === 'video' ? 6 + (i % 24) : null;
  const sha256 = crypto.createHash('sha256').update(sourceFile ?? name).digest('hex');
  const dhash = crypto.createHash('md5').update(sha256).digest('hex').slice(0, 16);
  const createdAgoMs = (i * 37 + (i % 5) * 3) * 86400000; // spread over ~ 6 months
  const created_at = new Date(now - createdAgoMs).toISOString();

  items.push({
    id: i + 1,
    _sourceFile: sourceFile,
    path: sourceFile ?? `D:\\Projects\\atomik-meme\\output\\${category.folder}\\${name}`,
    rel_path: sourceFile ? path.relative(LIBRARY_ROOT, sourceFile).split(path.sep).join('/') : `${category.folder}/${name}`,
    name,
    stem,
    ext,
    media_type,
    size_bytes,
    width: dims.width,
    height: dims.height,
    aspect_ratio: +(dims.width / dims.height).toFixed(3),
    duration_s,
    fps: media_type === 'video' ? 24 : null,
    created_at,
    modified_at: created_at,
    indexed_at: new Date(now - createdAgoMs / 4).toISOString(),
    sha256,
    dhash,
    title: toTitleCase(`${adjective} ${noun} moment`),
    description: `${toTitleCase(adjective)} ${noun} facing an ${pick(rng, TOPIC_POOL)}-related situation, panel ${i + 1}.`,
    ocr_text: rng() > 0.45 ? `${noun.toUpperCase()}: WHY IS THIS HAPPENING TO ME` : '',
    tags,
    topics,
    tone,
    meme_type: pick(rng, MEME_TYPES),
    template: rng() > 0.7 ? `${noun}-template` : null,
    category,
    nsfw,
    confidence: +(0.7 + rng() * 0.3).toFixed(2),
    favorite: false,
    view_count: 0,
    last_viewed_at: null,
  });
}

// --- force a few duplicate groups (fixture data only) -----------------------------------
if (items.length >= 4) {
  // exact duplicate: item 2 shares sha256 with item 1 (0-indexed: items[1], items[2])
  items[2].sha256 = items[1].sha256;
  items[2].size_bytes = items[1].size_bytes;
  items[2].dhash = items[1].dhash;
}
if (items.length >= 20) {
  // near-duplicate trio: items 10,11,12 get dhash values a few bits apart
  const base = items[10].dhash;
  items[11].dhash = hexFlipBit(base, 2);
  items[12].dhash = hexFlipBit(hexFlipBit(base, 5), 9);
}

const itemsById = new Map(items.map((it) => [it.id, it]));

// ---------------------------------------------------------------------------
// In-memory mutable state
// ---------------------------------------------------------------------------

// `let`, not `const`: reassigned wholesale when switching libraries (see libraryStores below)
// so that favorites/views recorded in one library are invisible in another and reappear when
// switching back.
let favorites = new Set(); // sha256
let views = new Map(); // sha256 -> { count, last }
let collectionSeq = 1;
const savedCollections = []; // { id, name, query, filters, icon, created_at, updated_at }

// ---------------------------------------------------------------------------
// Library selection
// ---------------------------------------------------------------------------

let currentLibraryPath = MOCK_NO_LIBRARY ? null : LIBRARY_ROOT;
let nativePickerAvailable = true;
let pickerOpen = false;
let pickerRoundRobin = 0;

// Per-library isolated favorite/view state, keyed by library path. The entry for
// `currentLibraryPath` is always kept in sync with the live `favorites`/`views` bindings above
// (see switchToLibrary()).
const libraryStores = new Map();
if (currentLibraryPath) libraryStores.set(currentLibraryPath, { favorites, views });

// A small pool of paths the fake native-picker dialog cycles through.
const FAKE_PICKABLE_PATHS = [
  'D:\\Media\\memes-2024',
  'D:\\Projects\\atomik-meme\\archive-2025\\old-memes',
  LIBRARY_ROOT,
];

let recentLibraries = [
  { path: LIBRARY_ROOT, last_opened: new Date(now - 3600_000).toISOString() },
  { path: 'D:\\Media\\memes-2024', last_opened: new Date(now - 2 * 86400_000).toISOString() },
  // Deliberately not in FAKE_TREE and not a real path, to exercise the greyed-out "not found" UI.
  { path: 'C:\\Users\\demo\\Pictures\\OldMemes (deleted)', last_opened: new Date(now - 10 * 86400_000).toISOString() },
];

// A small fixed fake Windows drive tree for GET /api/fs/list — deliberately NOT the real
// filesystem, so browsing is deterministic across dev machines. Keys are canonical paths as
// produced by normalizeFakePath(): drive roots keep a trailing backslash ("C:\\"), everything
// else doesn't.
const FAKE_ROOTS = [
  { name: 'C:\\', path: 'C:\\' },
  { name: 'D:\\', path: 'D:\\' },
];

const FAKE_TREE = {
  'C:\\': ['Users', 'Program Files', 'Windows'],
  'C:\\Users': ['demo', 'Public'],
  'C:\\Users\\demo': ['Pictures', 'Documents', 'Downloads', 'Desktop'],
  'C:\\Users\\demo\\Pictures': ['Memes', 'Screenshots', 'Vacation 2025'],
  'C:\\Users\\demo\\Pictures\\Memes': [],
  'C:\\Users\\demo\\Pictures\\Screenshots': [],
  'C:\\Users\\demo\\Pictures\\Vacation 2025': [],
  'C:\\Users\\demo\\Documents': [],
  'C:\\Users\\demo\\Downloads': ['memes-export'],
  'C:\\Users\\demo\\Downloads\\memes-export': [],
  'C:\\Users\\demo\\Desktop': [],
  'C:\\Users\\Public': [],
  'C:\\Program Files': [],
  'C:\\Windows': [],
  'D:\\': ['Projects', 'Backups', 'Media'],
  'D:\\Projects': ['atomik-meme'],
  'D:\\Projects\\atomik-meme': ['output', 'input', 'archive-2025'],
  'D:\\Projects\\atomik-meme\\output': [],
  'D:\\Projects\\atomik-meme\\input': [],
  'D:\\Projects\\atomik-meme\\archive-2025': ['old-memes', 'sorted'],
  'D:\\Projects\\atomik-meme\\archive-2025\\old-memes': [],
  'D:\\Projects\\atomik-meme\\archive-2025\\sorted': [],
  'D:\\Backups': [],
  'D:\\Media': ['memes-2024', 'memes-2025'],
  'D:\\Media\\memes-2024': [],
  'D:\\Media\\memes-2025': [],
};

const HIDDEN_DIR_NAMES = new Set(['$Recycle.Bin', 'System Volume Information']);

/** Items visible under the current library — [] when no library is selected. */
function activeItems() {
  return currentLibraryPath === null ? [] : items;
}

function dataDirFor(libraryPath) {
  return `${libraryPath.replace(/\\+$/, '')}\\.meme-web`;
}

function isAbsoluteWindowsPath(p) {
  return typeof p === 'string' && /^[A-Za-z]:\\/.test(p);
}

/** Canonicalizes a user-supplied Windows-style path to match FAKE_TREE's key format. */
function normalizeFakePath(input) {
  if (typeof input !== 'string' || input.length === 0) return input;
  let p = input.trim().replace(/\//g, '\\');
  if (/^[A-Za-z]:\\?$/.test(p)) return p[0].toUpperCase() + ':\\'; // bare drive root
  p = p.replace(/\\+$/, '');
  if (/^[a-zA-Z]:\\/.test(p)) p = p[0].toUpperCase() + p.slice(1);
  return p;
}

function fakeParentPath(p) {
  if (/^[A-Za-z]:\\$/.test(p)) return null; // drive root has no parent
  const idx = p.lastIndexOf('\\');
  return idx <= 2 ? p.slice(0, 3) : p.slice(0, idx);
}

/** Swaps the live favorites/views bindings to the target library's isolated store, creating
 * one on first visit. Persists the outgoing library's store so switching back restores it. */
function switchToLibrary(libraryPath) {
  if (currentLibraryPath) libraryStores.set(currentLibraryPath, { favorites, views });
  currentLibraryPath = libraryPath;
  const store = libraryStores.get(libraryPath) ?? { favorites: new Set(), views: new Map() };
  libraryStores.set(libraryPath, store);
  favorites = store.favorites;
  views = store.views;
  recentLibraries = [
    { path: libraryPath, last_opened: new Date().toISOString() },
    ...recentLibraries.filter((r) => r.path !== libraryPath),
  ].slice(0, 10);
}

function recentLibraryExists(p) {
  return p === LIBRARY_ROOT || Object.prototype.hasOwnProperty.call(FAKE_TREE, normalizeFakePath(p)) || fs.existsSync(p);
}

const scanState = {
  running: false,
  progress: null,
  last: {
    id: 1,
    started_at: new Date(now - 3600_000).toISOString(),
    finished_at: new Date(now - 3600_000 + 4000).toISOString(),
    total: items.length,
    added: items.length,
    modified: 0,
    removed: 0,
    moved: 0,
    duration_s: 4.0,
    error: null,
  },
};

/** Simulates a scan over ~4s (8 steps x 500ms), used both by POST /api/scan and by a library
 * switch ("start a background scan ... scan_on_start
 * semantics"). Shared so both call sites agree on timing/shape. */
function startSimulatedScan() {
  scanState.running = true;
  const total = activeItems().length;
  scanState.progress = { scanned: 0, total, added: 0, modified: 0, removed: 0, moved: 0 };
  const startedAt = new Date().toISOString();
  const steps = 8;
  let step = 0;
  const timer = setInterval(() => {
    step++;
    scanState.progress = {
      scanned: Math.min(total, Math.round((total * step) / steps)),
      total,
      added: step >= steps ? Math.min(2, total) : 0,
      modified: 0,
      removed: 0,
      moved: 0,
    };
    if (step >= steps) {
      clearInterval(timer);
      scanState.running = false;
      scanState.last = {
        id: scanState.last.id + 1,
        started_at: startedAt,
        finished_at: new Date().toISOString(),
        total,
        added: scanState.progress.added,
        modified: 0,
        removed: 0,
        moved: 0,
        duration_s: 4.0,
        error: null,
      };
    }
  }, 500);
}

function hammingDistanceHex(a, b) {
  let dist = 0;
  const len = Math.max(a.length, b.length);
  for (let i = 0; i < len; i++) {
    const na = parseInt(a[i] ?? '0', 16);
    const nb = parseInt(b[i] ?? '0', 16);
    let x = na ^ nb;
    while (x) {
      dist += x & 1;
      x >>= 1;
    }
  }
  return dist;
}

// ---------------------------------------------------------------------------
// Serialization
// ---------------------------------------------------------------------------

function toApiItem(it) {
  const v = views.get(it.sha256);
  return {
    id: it.id,
    path: it.path,
    rel_path: it.rel_path,
    name: it.name,
    stem: it.stem,
    ext: it.ext,
    media_type: it.media_type,
    size_bytes: it.size_bytes,
    width: it.width,
    height: it.height,
    aspect_ratio: it.aspect_ratio,
    duration_s: it.duration_s,
    fps: it.fps,
    created_at: it.created_at,
    modified_at: it.modified_at,
    indexed_at: it.indexed_at,
    sha256: it.sha256,
    dhash: it.dhash,
    title: it.title,
    description: it.description,
    ocr_text: it.ocr_text,
    tags: it.tags,
    topics: it.topics,
    tone: it.tone,
    meme_type: it.meme_type,
    template: it.template,
    category: it.category,
    nsfw: it.nsfw,
    confidence: it.confidence,
    favorite: favorites.has(it.sha256),
    view_count: v?.count ?? 0,
    last_viewed_at: v?.last ?? null,
    thumb_url: `/api/items/${it.id}/thumb?w=320`,
    media_url: `/api/items/${it.id}/media`,
  };
}

// ---------------------------------------------------------------------------
// Query parsing + operator extraction
// ---------------------------------------------------------------------------

function extractOperators(qRaw) {
  const filters = {};
  const text = (qRaw ?? '').replace(/\b(type|cat|tag|is):(\S+)/gi, (_m, key, val) => {
    const k = key.toLowerCase();
    if (k === 'type') filters.type = (filters.type ?? []).concat(val.toLowerCase());
    else if (k === 'cat') filters.category = (filters.category ?? []).concat(val);
    else if (k === 'tag') filters.tag = (filters.tag ?? []).concat(val);
    else if (k === 'is' && val.toLowerCase() === 'fav') filters.favorite = true;
    else if (k === 'is' && val.toLowerCase() === 'nsfw') filters.nsfwOnly = true;
    return '';
  });
  return { text: text.trim(), filters };
}

function matchesText(it, q) {
  const haystack = [
    it.name,
    it.title ?? '',
    it.description ?? '',
    it.ocr_text ?? '',
    it.tags.join(' '),
    it.topics.join(' '),
    it.category?.name ?? '',
  ]
    .join(' ')
    .toLowerCase();
  return haystack.includes(q.toLowerCase());
}

function fuzzyScore(it, q) {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const haystack = [it.name, it.title ?? '', it.tags.join(' ')].join(' ').toLowerCase();
  return words.filter((w) => haystack.includes(w)).length;
}

function filterItems(params) {
  const { text, filters: opFilters } = extractOperators(params.q);
  let out = activeItems().slice();

  const types = (params.type ? params.type.split(',') : []).concat(opFilters.type ?? []).filter(Boolean);
  if (types.length) out = out.filter((it) => types.includes(it.media_type));

  const categories = (params.category ? params.category.split(',') : []).concat(opFilters.category ?? []).filter(Boolean);
  if (categories.length) out = out.filter((it) => it.category && categories.includes(it.category.name));

  const tags = (params.tag ? params.tag.split(',') : []).concat(opFilters.tag ?? []).filter(Boolean);
  if (tags.length) out = out.filter((it) => tags.every((t) => it.tags.includes(t)));

  if (params.favorite === 'true' || opFilters.favorite) {
    out = out.filter((it) => favorites.has(it.sha256));
  }

  const nsfwMode = params.nsfw ?? 'exclude';
  if (opFilters.nsfwOnly || nsfwMode === 'only') out = out.filter((it) => it.nsfw);
  else if (nsfwMode === 'exclude') out = out.filter((it) => !it.nsfw);

  if (params.min_duration) out = out.filter((it) => (it.duration_s ?? 0) >= Number(params.min_duration));
  if (params.max_duration) out = out.filter((it) => (it.duration_s ?? Infinity) <= Number(params.max_duration));
  if (params.since) {
    const since = new Date(params.since).getTime();
    if (!Number.isNaN(since)) out = out.filter((it) => new Date(it.created_at).getTime() >= since);
  }

  let fuzzyUsed = false;
  if (text.length >= 1) {
    let matched = out.filter((it) => matchesText(it, text));
    if (text.length >= 3 && matched.length < 5) {
      const scored = out
        .filter((it) => !matched.includes(it))
        .map((it) => ({ it, score: fuzzyScore(it, text) }))
        .filter((s) => s.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, 50 - matched.length)
        .map((s) => s.it);
      if (scored.length) {
        matched = matched.concat(scored);
        fuzzyUsed = true;
      }
    } else if (text.length < 3) {
      matched = out.filter((it) => it.name.toLowerCase().includes(text.toLowerCase()) || (it.title ?? '').toLowerCase().includes(text.toLowerCase()));
    }
    out = matched;
  }

  const sort = params.sort || (text ? 'relevance' : 'newest');
  const sorted = out.slice();
  switch (sort) {
    case 'oldest':
      sorted.sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
      break;
    case 'name':
      sorted.sort((a, b) => a.name.localeCompare(b.name));
      break;
    case 'size':
      sorted.sort((a, b) => b.size_bytes - a.size_bytes);
      break;
    case 'duration':
      sorted.sort((a, b) => (b.duration_s ?? -1) - (a.duration_s ?? -1));
      break;
    case 'random': {
      const seed = Number(params.seed) || 42;
      const rng = mulberry32(seed);
      const withKey = sorted.map((it) => ({ it, k: rng() }));
      withKey.sort((a, b) => a.k - b.k);
      return { items: withKey.map((w) => w.it), fuzzyUsed };
    }
    case 'relevance':
      // fixture data has no real bm25 score; keep match/fuzzy order, exact matches first
      break;
    case 'newest':
    default:
      sorted.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
      break;
  }
  return { items: sorted, fuzzyUsed };
}

// ---------------------------------------------------------------------------
// HTTP plumbing
// ---------------------------------------------------------------------------

function sendJson(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(payload),
  });
  res.end(payload);
}

function sendError(res, status, detail) {
  sendJson(res, status, { detail });
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

async function readJson(req) {
  const buf = await readBody(req);
  if (buf.length === 0) return {};
  try {
    return JSON.parse(buf.toString('utf-8'));
  } catch {
    return {};
  }
}

const MIME = { '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.mp4': 'video/mp4', '.svg': 'image/svg+xml' };

function videoPlaceholderSvg(title) {
  const label = (title ?? 'video').slice(0, 28).replace(/[<&>]/g, '');
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 240">
    <rect width="320" height="240" fill="#1b1c22"/>
    <circle cx="160" cy="104" r="34" fill="#6c5ce7"/>
    <path d="M148 86l34 18-34 18z" fill="white"/>
    <text x="160" y="176" fill="#a4a8b4" font-family="sans-serif" font-size="13" text-anchor="middle">${label}</text>
  </svg>`;
}

async function serveFileWithRange(req, res, filePath, mime) {
  let stat;
  try {
    stat = await fsp.stat(filePath);
  } catch {
    sendError(res, 404, 'Media file not found on disk');
    return;
  }
  const range = req.headers.range;
  if (range) {
    const match = /bytes=(\d*)-(\d*)/.exec(range);
    if (match) {
      const start = match[1] ? parseInt(match[1], 10) : 0;
      const end = match[2] ? parseInt(match[2], 10) : stat.size - 1;
      const chunkSize = end - start + 1;
      res.writeHead(206, {
        'Content-Type': mime,
        'Content-Range': `bytes ${start}-${end}/${stat.size}`,
        'Accept-Ranges': 'bytes',
        'Content-Length': chunkSize,
        'Content-Disposition': 'inline',
        'Cache-Control': 'public, max-age=31536000, immutable',
      });
      fs.createReadStream(filePath, { start, end }).pipe(res);
      return;
    }
  }
  res.writeHead(200, {
    'Content-Type': mime,
    'Accept-Ranges': 'bytes',
    'Content-Length': stat.size,
    'Content-Disposition': 'inline',
    'Cache-Control': 'public, max-age=31536000, immutable',
  });
  fs.createReadStream(filePath).pipe(res);
}

// --- minimal multipart/form-data parser (no dependencies) -------------------------------
function parseMultipart(buffer, boundary) {
  const boundaryBuf = Buffer.from(`--${boundary}`);
  const parts = [];
  let start = buffer.indexOf(boundaryBuf);
  while (start !== -1) {
    const next = buffer.indexOf(boundaryBuf, start + boundaryBuf.length);
    if (next === -1) break;
    let chunk = buffer.slice(start + boundaryBuf.length, next);
    // strip leading CRLF and trailing CRLF before next boundary
    if (chunk.slice(0, 2).toString() === '\r\n') chunk = chunk.slice(2);
    if (chunk.slice(-2).toString() === '\r\n') chunk = chunk.slice(0, -2);
    const headerEnd = chunk.indexOf('\r\n\r\n');
    if (headerEnd !== -1) {
      const headerText = chunk.slice(0, headerEnd).toString('utf-8');
      const body = chunk.slice(headerEnd + 4);
      const nameMatch = /name="([^"]+)"/.exec(headerText);
      const filenameMatch = /filename="([^"]*)"/.exec(headerText);
      if (nameMatch) {
        parts.push({ name: nameMatch[1], filename: filenameMatch ? filenameMatch[1] : undefined, data: body });
      }
    }
    start = next;
  }
  return parts;
}

function sanitizeFilename(name) {
  const base = path.basename(name || 'upload').replace(/[\\/]/g, '_');
  return base.replace(/[^a-zA-Z0-9._-]/g, '_') || 'upload';
}

async function uniqueDestination(dir, filename) {
  const ext = path.extname(filename);
  const stem = filename.slice(0, filename.length - ext.length);
  let candidate = filename;
  let n = 1;
  while (true) {
    try {
      await fsp.access(path.join(dir, candidate));
      candidate = `${stem}-${n}${ext}`;
      n++;
    } catch {
      return candidate;
    }
  }
}

// ---------------------------------------------------------------------------
// Route handlers
// ---------------------------------------------------------------------------

function buildStats() {
  const by_type = { image: 0, gif: 0, video: 0 };
  let total_bytes = 0;
  let nsfwCount = 0;
  const catCounts = new Map();
  for (const it of activeItems()) {
    by_type[it.media_type]++;
    total_bytes += it.size_bytes;
    if (it.nsfw) nsfwCount++;
    if (it.category) {
      const key = it.category.name;
      const c = catCounts.get(key) ?? { name: it.category.name, folder: it.category.folder, count: 0 };
      c.count++;
      catCounts.set(key, c);
    }
  }
  return {
    total: activeItems().length,
    by_type,
    favorites: activeItems().filter((it) => favorites.has(it.sha256)).length,
    total_bytes,
    nsfw: nsfwCount,
    categories: [...catCounts.values()],
    last_scan: scanState.last,
    scanning: scanState.running,
  };
}

function buildBuiltinCollections() {
  const stats = buildStats();
  const recentAdded = activeItems().filter((it) => now - new Date(it.indexed_at).getTime() <= 7 * 86400000).length;
  const recentViewed = activeItems().filter((it) => views.has(it.sha256)).length;
  const builtins = [
    { id: 'builtin:favorites', name: 'Favorites', query: '', filters: { favorite: true }, icon: '♥', builtin: true, count: stats.favorites },
    { id: 'builtin:recent-added', name: 'Recently added', query: '', filters: { sort: 'newest' }, icon: '🕒', builtin: true, count: recentAdded },
    { id: 'builtin:recent-viewed', name: 'Recently viewed', query: '', filters: {}, icon: '👁', builtin: true, count: recentViewed },
    { id: 'builtin:images', name: 'Images', query: '', filters: { type: ['image'] }, icon: '🖼', builtin: true, count: stats.by_type.image },
    { id: 'builtin:gifs', name: 'GIFs', query: '', filters: { type: ['gif'] }, icon: '🎞', builtin: true, count: stats.by_type.gif },
    { id: 'builtin:videos', name: 'Videos', query: '', filters: { type: ['video'] }, icon: '▶', builtin: true, count: stats.by_type.video },
    { id: 'builtin:nsfw', name: 'NSFW', query: '', filters: { nsfw: 'only' }, icon: '🔞', builtin: true, count: stats.nsfw },
    { id: 'builtin:duplicates', name: 'Duplicates', query: '', filters: {}, icon: '⧉', builtin: true },
  ];
  for (const c of stats.categories) {
    builtins.push({
      id: `category:${c.name}`,
      name: c.name,
      query: '',
      filters: { category: [c.name] },
      icon: '▤',
      builtin: true,
      count: c.count,
    });
  }
  return builtins;
}

function computeDuplicateGroups(near, maxDistance) {
  const groups = [];
  const seenExact = new Set();
  const bySha = new Map();
  for (const it of activeItems()) {
    if (!bySha.has(it.sha256)) bySha.set(it.sha256, []);
    bySha.get(it.sha256).push(it);
  }
  for (const [sha, group] of bySha) {
    if (group.length > 1) {
      groups.push({ kind: 'exact', distance: 0, items: group.map(toApiItem) });
      seenExact.add(sha);
    }
  }
  if (near) {
    const singles = activeItems().filter((it) => !seenExact.has(it.sha256));
    const used = new Set();
    for (let i = 0; i < singles.length; i++) {
      if (used.has(singles[i].id)) continue;
      const cluster = [singles[i]];
      for (let j = i + 1; j < singles.length; j++) {
        if (used.has(singles[j].id)) continue;
        const d = hammingDistanceHex(singles[i].dhash, singles[j].dhash);
        if (d > 0 && d <= maxDistance) {
          cluster.push(singles[j]);
          used.add(singles[j].id);
        }
      }
      if (cluster.length > 1) {
        used.add(singles[i].id);
        const maxD = Math.max(...cluster.slice(1).map((it) => hammingDistanceHex(cluster[0].dhash, it.dhash)));
        groups.push({ kind: 'near', distance: maxD, items: cluster.map(toApiItem) });
      }
    }
  }
  return groups;
}

async function handleInbox(req, res) {
  if (!INBOX_ENABLED) {
    sendError(res, 403, 'Inbox uploads are disabled');
    return;
  }
  const contentType = req.headers['content-type'] || '';
  const boundaryMatch = /boundary=(?:"([^"]+)"|([^;]+))/.exec(contentType);
  if (!boundaryMatch) {
    sendError(res, 400, 'Expected multipart/form-data with a boundary');
    return;
  }
  const boundary = boundaryMatch[1] || boundaryMatch[2];
  const buf = await readBody(req);
  const parts = parseMultipart(buf, boundary).filter((p) => p.name === 'files' && p.filename);
  if (parts.length === 0) {
    sendError(res, 400, 'No files provided');
    return;
  }
  await fsp.mkdir(INBOX_DIR, { recursive: true });
  const saved = [];
  for (const part of parts) {
    const safeName = sanitizeFilename(part.filename);
    const dest = await uniqueDestination(INBOX_DIR, safeName);
    await fsp.writeFile(path.join(INBOX_DIR, dest), part.data);
    saved.push(dest);
  }
  sendJson(res, 200, { saved, inbox_dir: INBOX_DIR });
}

// ---------------------------------------------------------------------------
// Server
// ---------------------------------------------------------------------------

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host}`);
    const pathname = url.pathname;
    const params = Object.fromEntries(url.searchParams.entries());
    const method = req.method;

    // Simulate a small, realistic amount of server latency for search-ish endpoints.
    const withLatency = async (fn) => {
      const t0 = Date.now();
      const result = await fn();
      return { result, took_ms: Date.now() - t0 + Math.random() * 8 };
    };

    if (pathname === '/api/health' && method === 'GET') {
      sendJson(res, 200, {
        status: 'ok',
        version: 'mock-0.1.0',
        library_root: currentLibraryPath,
        data_dir: currentLibraryPath ? dataDirFor(currentLibraryPath) : '',
        ffmpeg: true,
        items: activeItems().length,
      });
      return;
    }

    if (pathname === '/api/config' && method === 'GET') {
      sendJson(res, 200, {
        inbox_enabled: INBOX_ENABLED,
        nsfw_default: 'hide',
        thumb_sizes: [320, 640],
        library_root: currentLibraryPath,
      });
      return;
    }

    if (pathname === '/api/stats' && method === 'GET') {
      sendJson(res, 200, buildStats());
      return;
    }

    if (pathname === '/api/items' && method === 'GET') {
      const { result, took_ms } = await withLatency(() => filterItems(params));
      const limit = Math.min(500, Math.max(1, Number(params.limit) || 100));
      const offset = Math.max(0, Number(params.offset) || 0);
      const page = result.items.slice(offset, offset + limit);
      sendJson(res, 200, {
        items: page.map(toApiItem),
        total: result.items.length,
        limit,
        offset,
        took_ms,
        fuzzy_used: result.fuzzyUsed,
      });
      return;
    }

    if (pathname === '/api/random' && method === 'GET') {
      const { result } = await withLatency(() => filterItems(params));
      if (result.items.length === 0) {
        sendError(res, 404, 'No items match the current filters');
        return;
      }
      const pickIdx = Math.floor(Math.random() * result.items.length);
      sendJson(res, 200, toApiItem(result.items[pickIdx]));
      return;
    }

    if (pathname === '/api/recent' && method === 'GET') {
      const kind = params.kind === 'viewed' ? 'viewed' : 'added';
      const limit = Number(params.limit) || 50;
      let list;
      if (kind === 'viewed') {
        list = activeItems()
          .filter((it) => views.has(it.sha256))
          .sort((a, b) => new Date(views.get(b.sha256).last) - new Date(views.get(a.sha256).last));
      } else {
        list = activeItems()
          .slice()
          .sort((a, b) => new Date(b.indexed_at) - new Date(a.indexed_at));
      }
      sendJson(res, 200, { items: list.slice(0, limit).map(toApiItem) });
      return;
    }

    if (pathname === '/api/tags' && method === 'GET') {
      const q = (params.q ?? '').toLowerCase();
      const limit = Number(params.limit) || 50;
      const counts = new Map();
      for (const it of activeItems()) {
        for (const tag of it.tags) {
          if (q && !tag.toLowerCase().includes(q)) continue;
          counts.set(tag, (counts.get(tag) ?? 0) + 1);
        }
      }
      const tags = [...counts.entries()]
        .map(([tag, count]) => ({ tag, count }))
        .sort((a, b) => b.count - a.count)
        .slice(0, limit);
      sendJson(res, 200, { tags });
      return;
    }

    if (pathname === '/api/categories' && method === 'GET') {
      sendJson(res, 200, { categories: buildStats().categories.map((c, i) => ({ id: i + 1, name: c.name, folder: c.folder, count: c.count, pinned: false })) });
      return;
    }

    if (pathname === '/api/suggest' && method === 'GET') {
      const q = (params.q ?? '').toLowerCase();
      const limit = Number(params.limit) || 8;
      const suggestions = [];
      const seen = new Set();
      const pushUnique = (s) => {
        const key = `${s.kind}:${s.text}`;
        if (!seen.has(key)) {
          seen.add(key);
          suggestions.push(s);
        }
      };
      if (q.length >= 1) {
        for (const it of activeItems()) {
          if (suggestions.length >= limit) break;
          for (const tag of it.tags) {
            if (tag.toLowerCase().includes(q)) pushUnique({ kind: 'tag', text: tag });
          }
        }
        for (const it of activeItems()) {
          if (suggestions.length >= limit) break;
          if (it.category && it.category.name.toLowerCase().includes(q)) {
            pushUnique({ kind: 'category', text: it.category.name });
          }
        }
        for (const it of activeItems()) {
          if (suggestions.length >= limit) break;
          if ((it.title ?? '').toLowerCase().includes(q)) {
            pushUnique({ kind: 'title', text: it.title, item_id: it.id });
          }
        }
        for (const it of activeItems()) {
          if (suggestions.length >= limit) break;
          if (it.name.toLowerCase().includes(q)) {
            pushUnique({ kind: 'file', text: it.name, item_id: it.id });
          }
        }
      }
      sendJson(res, 200, { suggestions: suggestions.slice(0, limit) });
      return;
    }

    if (pathname === '/api/duplicates' && method === 'GET') {
      const near = params.near !== 'false';
      const maxDistance = Number(params.max_distance) || 6;
      sendJson(res, 200, { groups: computeDuplicateGroups(near, maxDistance), computed_at: new Date().toISOString() });
      return;
    }

    if (pathname === '/api/collections' && method === 'GET') {
      sendJson(res, 200, { collections: [...buildBuiltinCollections(), ...savedCollections] });
      return;
    }

    if (pathname === '/api/collections' && method === 'POST') {
      const body = await readJson(req);
      const collection = {
        id: collectionSeq++,
        name: String(body.name ?? 'Untitled'),
        query: String(body.query ?? ''),
        filters: body.filters ?? {},
        icon: body.icon ?? null,
        builtin: false,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      savedCollections.push(collection);
      sendJson(res, 201, collection);
      return;
    }

    const collectionMatch = /^\/api\/collections\/([^/]+)$/.exec(pathname);
    if (collectionMatch && (method === 'PUT' || method === 'DELETE')) {
      const id = collectionMatch[1];
      if (id.startsWith('builtin:') || id.startsWith('category:')) {
        sendError(res, 400, 'Built-in collections cannot be modified');
        return;
      }
      const idx = savedCollections.findIndex((c) => String(c.id) === id);
      if (idx === -1) {
        sendError(res, 404, 'Collection not found');
        return;
      }
      if (method === 'DELETE') {
        savedCollections.splice(idx, 1);
        res.writeHead(204).end();
        return;
      }
      const body = await readJson(req);
      const existing = savedCollections[idx];
      const updated = {
        ...existing,
        name: String(body.name ?? existing.name),
        query: String(body.query ?? existing.query),
        filters: body.filters ?? existing.filters,
        icon: body.icon ?? existing.icon,
        updated_at: new Date().toISOString(),
      };
      savedCollections[idx] = updated;
      sendJson(res, 200, updated);
      return;
    }

    if (pathname === '/api/scan/status' && method === 'GET') {
      sendJson(res, 200, { running: scanState.running, progress: scanState.progress, last: scanState.last });
      return;
    }

    if (pathname === '/api/scan' && method === 'POST') {
      if (scanState.running) {
        sendJson(res, 409, { running: true });
        return;
      }
      startSimulatedScan();
      sendJson(res, 202, { started: true });
      return;
    }

    // --- Library selection --------------------------------

    if (pathname === '/api/fs/list' && method === 'GET') {
      const rawPath = params.path;
      if (!rawPath) {
        sendJson(res, 200, { path: null, parent: null, entries: [], roots: FAKE_ROOTS });
        return;
      }
      if (!isAbsoluteWindowsPath(rawPath)) {
        sendError(res, 400, 'Path must be absolute');
        return;
      }
      const normalized = normalizeFakePath(rawPath);
      const children = FAKE_TREE[normalized];
      if (children === undefined) {
        sendError(res, 404, 'Path not found');
        return;
      }
      const visible = children.filter((name) => !name.startsWith('.') && !HIDDEN_DIR_NAMES.has(name));
      const sorted = visible.slice().sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }));
      const truncated = sorted.length > 500;
      const entries = sorted.slice(0, 500).map((name) => {
        const childPath = normalized.endsWith('\\') ? `${normalized}${name}` : `${normalized}\\${name}`;
        return { name, path: childPath, has_children: (FAKE_TREE[childPath]?.length ?? 0) > 0 };
      });
      sendJson(res, 200, {
        path: normalized,
        parent: fakeParentPath(normalized),
        entries,
        roots: FAKE_ROOTS,
        ...(truncated ? { truncated: true } : {}),
      });
      return;
    }

    if (pathname === '/api/library/pick' && method === 'POST') {
      if (!nativePickerAvailable) {
        sendError(res, 501, 'Native folder dialog unavailable in this environment');
        return;
      }
      if (pickerOpen) {
        sendError(res, 409, 'A folder picker is already open');
        return;
      }
      pickerOpen = true;
      setTimeout(() => {
        pickerOpen = false;
        // Every 4th pick simulates the user dismissing the dialog without choosing anything.
        const cancelled = pickerRoundRobin % 4 === 3;
        const chosen = FAKE_PICKABLE_PATHS[pickerRoundRobin % FAKE_PICKABLE_PATHS.length];
        pickerRoundRobin++;
        if (cancelled) {
          res.writeHead(204).end();
          return;
        }
        sendJson(res, 200, { path: chosen });
      }, 1000);
      return;
    }

    if (pathname === '/api/library/reveal' && method === 'POST') {
      console.log(`[atomik-meme-web mock] (simulated) reveal library ${currentLibraryPath}`);
      sendJson(res, 200, { ok: true });
      return;
    }

    if (pathname === '/api/library/recent' && method === 'DELETE') {
      const p = params.path;
      recentLibraries = recentLibraries.filter((r) => r.path !== p);
      res.writeHead(204).end();
      return;
    }

    if (pathname === '/api/library' && method === 'GET') {
      sendJson(res, 200, {
        library_root: currentLibraryPath,
        data_dir: currentLibraryPath ? dataDirFor(currentLibraryPath) : '',
        recent: recentLibraries.map((r) => ({ ...r, exists: recentLibraryExists(r.path) })),
        native_picker: nativePickerAvailable,
        remember: true,
      });
      return;
    }

    if (pathname === '/api/library' && method === 'POST') {
      const body = await readJson(req);
      const rawPath = typeof body.path === 'string' ? body.path.trim() : '';
      if (!isAbsoluteWindowsPath(rawPath)) {
        sendError(res, 400, 'Path must be absolute');
        return;
      }
      const normalized = normalizeFakePath(rawPath);
      const inFakeTree = Object.prototype.hasOwnProperty.call(FAKE_TREE, normalized);
      const isRealLibrary = normalized === normalizeFakePath(LIBRARY_ROOT);
      let resolvedPath;
      if (isRealLibrary) {
        resolvedPath = LIBRARY_ROOT;
      } else if (inFakeTree) {
        resolvedPath = normalized;
      } else {
        // Not in the fake tree — fall back to the real filesystem so a user can type/paste an
        // actual folder path (e.g. their real memes folder) and have it validate for real.
        let stat;
        try {
          stat = fs.statSync(rawPath);
        } catch {
          stat = null;
        }
        if (!stat || !stat.isDirectory()) {
          sendError(res, 404, 'Path does not exist or is not a directory');
          return;
        }
        resolvedPath = rawPath;
      }

      if (resolvedPath === currentLibraryPath) {
        sendJson(res, 200, { library_root: resolvedPath, data_dir: dataDirFor(resolvedPath), scan_started: false });
        return;
      }

      if (scanState.running) {
        // Best-effort cooperative cancel: the mock has no real indexer thread to signal, so it
        // just stops the simulated progress immediately rather than returning 409.
        scanState.running = false;
        scanState.progress = null;
      }

      switchToLibrary(resolvedPath);
      // A drive root is the mock's stand-in for "quick sample suggests > 100k files".
      const warning = /^[A-Za-z]:\\$/.test(resolvedPath) ? 'large folder' : undefined;
      startSimulatedScan();

      sendJson(res, 200, {
        library_root: resolvedPath,
        data_dir: dataDirFor(resolvedPath),
        scan_started: true,
        ...(warning ? { warning } : {}),
      });
      return;
    }

    if (pathname === '/api/inbox' && method === 'POST') {
      await handleInbox(req, res);
      return;
    }

    // --- /api/items/{id}... ---------------------------------------------------------
    const itemMatch = /^\/api\/items\/(\d+)(?:\/(thumb|media|view|favorite|open|reveal))?$/.exec(pathname);
    if (itemMatch) {
      const id = Number(itemMatch[1]);
      const sub = itemMatch[2];
      const item = itemsById.get(id);
      if (!item) {
        sendError(res, 404, 'Item not found');
        return;
      }

      if (!sub && method === 'GET') {
        const duplicates = items.filter((it) => it.id !== item.id && it.sha256 === item.sha256).map(toApiItem);
        sendJson(res, 200, { ...toApiItem(item), sidecar: null, duplicates });
        return;
      }

      if (sub === 'thumb' && method === 'GET') {
        if (item.media_type === 'video') {
          const svg = videoPlaceholderSvg(item.title);
          res.writeHead(200, { 'Content-Type': 'image/svg+xml', 'Cache-Control': 'public, max-age=31536000, immutable' });
          res.end(svg);
          return;
        }
        if (!item._sourceFile) {
          sendError(res, 404, 'No thumbnail available');
          return;
        }
        await serveFileWithRange(req, res, item._sourceFile, MIME[item.ext] ?? 'application/octet-stream');
        return;
      }

      if (sub === 'media' && method === 'GET') {
        if (!item._sourceFile) {
          sendError(res, 404, 'No media file available');
          return;
        }
        await serveFileWithRange(req, res, item._sourceFile, MIME[item.ext] ?? 'application/octet-stream');
        return;
      }

      if (sub === 'view' && method === 'POST') {
        const entry = views.get(item.sha256) ?? { count: 0, last: null };
        entry.count += 1;
        entry.last = new Date().toISOString();
        views.set(item.sha256, entry);
        sendJson(res, 200, { view_count: entry.count, last_viewed_at: entry.last });
        return;
      }

      if (sub === 'favorite' && method === 'PUT') {
        const body = await readJson(req);
        if (body.favorite) favorites.add(item.sha256);
        else favorites.delete(item.sha256);
        sendJson(res, 200, { favorite: favorites.has(item.sha256) });
        return;
      }

      if (sub === 'open' && method === 'POST') {
        console.log(`[atomik-meme-web mock] (simulated) open ${item.path}`);
        sendJson(res, 200, { ok: true });
        return;
      }

      if (sub === 'reveal' && method === 'POST') {
        console.log(`[atomik-meme-web mock] (simulated) reveal ${item.path}`);
        sendJson(res, 200, { ok: true });
        return;
      }
    }

    if (pathname.startsWith('/api/')) {
      sendError(res, 404, 'Not found');
      return;
    }

    sendError(res, 404, 'This mock server only implements /api routes; run `npm run dev` for the SPA.');
  } catch (err) {
    console.error(err);
    sendError(res, 500, err instanceof Error ? err.message : 'Internal error');
  }
});

server.listen(PORT, HOST, () => {
  console.log(`[atomik-meme-web mock] Listening on http://${HOST}:${PORT}`);
  console.log(`[atomik-meme-web mock] ${items.length} fixture items, ${REAL_FILES.length} real files backing them`);
  console.log('[atomik-meme-web mock] Run `npm run dev` in another terminal and open http://localhost:5173');
});
