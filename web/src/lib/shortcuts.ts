// Keyboard shortcut map. Pure, DOM-event-in/action-out so it is
// trivially unit-testable; App.svelte wires the resulting actions to real behaviour.

export type ShortcutAction =
  | 'focus-search'
  | 'close-or-clear'
  | 'move-left'
  | 'move-right'
  | 'move-up'
  | 'move-down'
  | 'open'
  | 'favorite'
  | 'random'
  | 'copy-path'
  | 'copy-media'
  | 'open-original'
  | 'reveal'
  | 'toggle-info'
  | 'filter-image'
  | 'filter-gif'
  | 'filter-video'
  | 'clear-filters'
  | 'cycle-nsfw'
  | 'toggle-theme'
  | 'column-size-up'
  | 'column-size-down'
  | 'go-home'
  | 'go-end'
  | 'help'
  | 'open-library';

/** True when the event target is a place where typing should not trigger shortcuts. */
export function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  // jsdom (used in tests) doesn't fully implement isContentEditable, so also check the
  // attribute directly.
  if (target.isContentEditable || target.getAttribute('contenteditable') === 'true') return true;
  return false;
}

interface ShortcutDef {
  action: ShortcutAction;
  match: (e: KeyboardEvent) => boolean;
}

// Order matters: more specific matchers (e.g. Shift+C) must precede more general ones (c).
// None of these check ctrl/meta/alt themselves — matchShortcut() filters modified keystrokes
// (other than Ctrl/Cmd+K, handled separately) before consulting this table, so browser/OS
// combos like Ctrl+C or Ctrl+R are never hijacked.
const SHORTCUTS: ShortcutDef[] = [
  { action: 'focus-search', match: (e) => e.key === '/' },
  { action: 'help', match: (e) => e.key === '?' },
  { action: 'close-or-clear', match: (e) => e.key === 'Escape' },
  { action: 'move-left', match: (e) => e.key === 'ArrowLeft' },
  { action: 'move-right', match: (e) => e.key === 'ArrowRight' },
  { action: 'move-up', match: (e) => e.key === 'ArrowUp' },
  { action: 'move-down', match: (e) => e.key === 'ArrowDown' },
  { action: 'go-home', match: (e) => e.key === 'Home' },
  { action: 'go-end', match: (e) => e.key === 'End' },
  { action: 'open', match: (e) => e.key === 'Enter' || e.key === ' ' },
  { action: 'copy-media', match: (e) => e.key === 'C' && e.shiftKey },
  { action: 'favorite', match: (e) => e.key === 'f' },
  { action: 'random', match: (e) => e.key === 'r' },
  { action: 'copy-path', match: (e) => e.key === 'c' && !e.shiftKey },
  { action: 'open-original', match: (e) => e.key === 'o' },
  { action: 'reveal', match: (e) => e.key === 'e' },
  { action: 'toggle-info', match: (e) => e.key === 'i' },
  { action: 'filter-image', match: (e) => e.key === '1' },
  { action: 'filter-gif', match: (e) => e.key === '2' },
  { action: 'filter-video', match: (e) => e.key === '3' },
  { action: 'clear-filters', match: (e) => e.key === '0' },
  { action: 'cycle-nsfw', match: (e) => e.key === 'n' },
  { action: 'toggle-theme', match: (e) => e.key === 'd' },
  { action: 'column-size-up', match: (e) => e.key === '+' || e.key === '=' },
  { action: 'column-size-down', match: (e) => e.key === '-' || e.key === '_' },
];

/**
 * Resolve a keydown event to a shortcut action, or null if none matches or the event
 * originated from a typing surface (input/textarea/select/contenteditable) — shortcuts are
 * ignored while typing.
 */
export function matchShortcut(e: KeyboardEvent): ShortcutAction | null {
  if (isEditableTarget(e.target)) return null;
  const ctrlOrMeta = e.ctrlKey || e.metaKey;
  if (e.key.toLowerCase() === 'k' && ctrlOrMeta) return 'focus-search';
  if (e.key.toLowerCase() === 'o' && ctrlOrMeta) return 'open-library';
  if (ctrlOrMeta || e.altKey) return null; // don't hijack other modified combos
  for (const def of SHORTCUTS) {
    if (def.match(e)) return def.action;
  }
  return null;
}

export const SHORTCUT_HELP: { keys: string; description: string }[] = [
  { keys: '/  or  Ctrl+K', description: 'Focus search' },
  { keys: 'Ctrl+O', description: 'Choose / switch library' },
  { keys: 'Esc', description: 'Close viewer / clear search' },
  { keys: '← → ↑ ↓', description: 'Move focus in grid, or prev/next in lightbox' },
  { keys: 'Enter / Space', description: 'Open focused item' },
  { keys: 'f', description: 'Toggle favorite' },
  { keys: 'r', description: 'Random item' },
  { keys: 'c', description: 'Copy path' },
  { keys: 'Shift+C', description: 'Copy media' },
  { keys: 'o', description: 'Open original' },
  { keys: 'e', description: 'Reveal in folder' },
  { keys: 'i', description: 'Toggle info panel' },
  { keys: 'Wheel / dbl-click', description: 'Zoom image in viewer (drag to pan)' },
  { keys: '1 / 2 / 3', description: 'Toggle image / GIF / video filter' },
  { keys: '0', description: 'Clear filters' },
  { keys: 'n', description: 'Cycle NSFW mode' },
  { keys: 'd', description: 'Toggle theme' },
  { keys: '+ / -', description: 'Column size' },
  { keys: 'Home / End', description: 'Jump to start / end' },
  { keys: '?', description: 'Show this help' },
];
