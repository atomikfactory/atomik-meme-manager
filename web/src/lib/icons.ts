// Maps backend-supplied icon NAMES (Collection.icon, e.g. "heart") to renderable glyphs for the
// Sidebar. The backend sends semantic names, not emoji/glyphs, so without this mapping they'd
// render as literal words ("heart", "clock", …).
const ICON_NAME_TO_GLYPH: Record<string, string> = {
  heart: '♥',
  clock: '◷',
  eye: '◉',
  gif: 'GIF',
  video: '▶',
  image: '▣',
  warning: '⚠',
  copy: '⧉',
  folder: '▤',
  star: '★',
  shuffle: '⤮',
  tag: '#',
};

/**
 * Resolve a backend icon name to a glyph. Known names are mapped explicitly; an unknown value
 * that already looks like a glyph/emoji (1-2 characters) is passed through as-is; anything
 * longer (an unrecognised word) falls back to a generic bullet rather than being rendered as
 * literal text. Returns null for a missing/empty icon so callers can apply their own default.
 */
export function resolveIcon(icon: string | null | undefined): string | null {
  if (!icon) return null;
  const key = icon.trim().toLowerCase();
  if (key.length === 0) return null;
  if (key in ICON_NAME_TO_GLYPH) return ICON_NAME_TO_GLYPH[key];
  if (icon.length <= 2) return icon;
  return '•';
}
