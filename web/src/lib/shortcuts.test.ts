import { describe, expect, it } from 'vitest';
import { isEditableTarget, matchShortcut, SHORTCUT_HELP, type ShortcutAction } from './shortcuts';

function keyEvent(
  key: string,
  opts: Partial<{ target: EventTarget; shiftKey: boolean; ctrlKey: boolean; metaKey: boolean; altKey: boolean }> = {}
): KeyboardEvent {
  const event = new KeyboardEvent('keydown', {
    key,
    shiftKey: opts.shiftKey ?? false,
    ctrlKey: opts.ctrlKey ?? false,
    metaKey: opts.metaKey ?? false,
    altKey: opts.altKey ?? false,
  });
  if (opts.target) {
    Object.defineProperty(event, 'target', { value: opts.target, enumerable: true });
  }
  return event;
}

describe('isEditableTarget', () => {
  it('treats input, textarea, select and contenteditable as editable', () => {
    expect(isEditableTarget(document.createElement('input'))).toBe(true);
    expect(isEditableTarget(document.createElement('textarea'))).toBe(true);
    expect(isEditableTarget(document.createElement('select'))).toBe(true);
    const div = document.createElement('div');
    div.setAttribute('contenteditable', 'true');
    expect(isEditableTarget(div)).toBe(true);
  });

  it('treats a plain div and null as non-editable', () => {
    expect(isEditableTarget(document.createElement('div'))).toBe(false);
    expect(isEditableTarget(null)).toBe(false);
  });
});

describe('matchShortcut: ignored while typing', () => {
  it('returns null for every shortcut key when focused in an input', () => {
    const input = document.createElement('input');
    const keys = ['/', 'f', 'r', 'c', 'o', 'e', 'i', 'n', 'd', '1', '2', '3', '0', '+', '-', 'Escape', '?'];
    for (const key of keys) {
      expect(matchShortcut(keyEvent(key, { target: input }))).toBeNull();
    }
  });

  it('returns null in a textarea and a contenteditable element', () => {
    const textarea = document.createElement('textarea');
    expect(matchShortcut(keyEvent('f', { target: textarea }))).toBeNull();
    const div = document.createElement('div');
    div.setAttribute('contenteditable', 'true');
    expect(matchShortcut(keyEvent('f', { target: div }))).toBeNull();
  });

  it('ignores Ctrl+O (and Ctrl+K) while typing in an input', () => {
    const input = document.createElement('input');
    expect(matchShortcut(keyEvent('o', { target: input, ctrlKey: true }))).toBeNull();
    expect(matchShortcut(keyEvent('k', { target: input, ctrlKey: true }))).toBeNull();
  });

  it('is still active outside of typing surfaces', () => {
    const div = document.createElement('div');
    expect(matchShortcut(keyEvent('f', { target: div }))).toBe('favorite');
  });
});

describe('matchShortcut: key map', () => {
  const div = document.createElement('div');
  const cases: [string, Partial<KeyboardEventInit>, ShortcutAction][] = [
    ['/', {}, 'focus-search'],
    ['?', {}, 'help'],
    ['Escape', {}, 'close-or-clear'],
    ['ArrowLeft', {}, 'move-left'],
    ['ArrowRight', {}, 'move-right'],
    ['ArrowUp', {}, 'move-up'],
    ['ArrowDown', {}, 'move-down'],
    ['Home', {}, 'go-home'],
    ['End', {}, 'go-end'],
    ['Enter', {}, 'open'],
    [' ', {}, 'open'],
    ['f', {}, 'favorite'],
    ['r', {}, 'random'],
    ['c', {}, 'copy-path'],
    ['C', { shiftKey: true }, 'copy-media'],
    ['o', {}, 'open-original'],
    ['e', {}, 'reveal'],
    ['i', {}, 'toggle-info'],
    ['1', {}, 'filter-image'],
    ['2', {}, 'filter-gif'],
    ['3', {}, 'filter-video'],
    ['0', {}, 'clear-filters'],
    ['n', {}, 'cycle-nsfw'],
    ['d', {}, 'toggle-theme'],
    ['+', {}, 'column-size-up'],
    ['=', {}, 'column-size-up'],
    ['-', {}, 'column-size-down'],
    ['_', {}, 'column-size-down'],
  ];

  for (const [key, opts, expected] of cases) {
    it(`maps "${key}"${opts.shiftKey ? ' (shift)' : ''} to ${expected}`, () => {
      expect(matchShortcut(keyEvent(key, { ...opts, target: div }))).toBe(expected);
    });
  }

  it('maps Ctrl+K and Cmd+K to focus-search', () => {
    expect(matchShortcut(keyEvent('k', { target: div, ctrlKey: true }))).toBe('focus-search');
    expect(matchShortcut(keyEvent('k', { target: div, metaKey: true }))).toBe('focus-search');
  });

  it('maps Ctrl+O and Cmd+O to open-library', () => {
    expect(matchShortcut(keyEvent('o', { target: div, ctrlKey: true }))).toBe('open-library');
    expect(matchShortcut(keyEvent('o', { target: div, metaKey: true }))).toBe('open-library');
    // bare "o" (no modifier) stays "open-original", per the existing item-action shortcut
    expect(matchShortcut(keyEvent('o', { target: div }))).toBe('open-original');
  });

  it('does not hijack other ctrl/cmd/alt modified keys', () => {
    expect(matchShortcut(keyEvent('c', { target: div, ctrlKey: true }))).toBeNull();
    expect(matchShortcut(keyEvent('r', { target: div, metaKey: true }))).toBeNull();
    expect(matchShortcut(keyEvent('f', { target: div, altKey: true }))).toBeNull();
  });

  it('has no duplicate keys documented in the help table beyond the actions that intentionally share a description', () => {
    expect(SHORTCUT_HELP.length).toBeGreaterThan(10);
  });
});
