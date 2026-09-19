// Covers the grid keyboard-focus rules in state.svelte.ts: with no card focused yet
// (e.g. right after the app loads, before the user has clicked anything), the first arrow
// press must land on the first item rather than doing nothing — otherwise the keyboard-only
// flow "open app -> arrow -> f / Enter" is stuck until a mouse click sets a starting point.
import { beforeEach, describe, expect, it } from 'vitest';
import { focusEnd, focusHome, focusedItem, moveFocus, results, selection } from './state.svelte';
import type { Item } from './types';

function makeItem(id: number): Item {
  return {
    id,
    path: `C:\\fake\\${id}.jpg`,
    rel_path: `${id}.jpg`,
    name: `${id}.jpg`,
    stem: String(id),
    ext: '.jpg',
    media_type: 'image',
    size_bytes: 100,
    width: 100,
    height: 100,
    aspect_ratio: 1,
    duration_s: null,
    fps: null,
    created_at: '2026-01-01T00:00:00Z',
    modified_at: '2026-01-01T00:00:00Z',
    indexed_at: '2026-01-01T00:00:00Z',
    sha256: `sha-${id}`,
    dhash: null,
    title: `Item ${id}`,
    description: null,
    ocr_text: null,
    tags: [],
    topics: [],
    tone: [],
    meme_type: null,
    template: null,
    category: null,
    nsfw: false,
    confidence: null,
    favorite: false,
    view_count: 0,
    last_viewed_at: null,
    thumb_url: `/api/items/${id}/thumb`,
    media_url: `/api/items/${id}/media`,
  };
}

describe('grid keyboard focus: no-selection start', () => {
  beforeEach(() => {
    results.items = [1, 2, 3, 4, 5, 6].map(makeItem);
    results.total = results.items.length;
    selection.focusedId = null;
  });

  it('move-right with nothing focused selects the first item', () => {
    moveFocus(1);
    expect(selection.focusedId).toBe(1);
  });

  it('move-left with nothing focused ALSO selects the first item, not the last or an out-of-range one', () => {
    moveFocus(-1);
    expect(selection.focusedId).toBe(1);
  });

  it('move-down (positive column-count delta) with nothing focused selects the first item', () => {
    moveFocus(3); // e.g. a 3-column grid's "down" delta
    expect(selection.focusedId).toBe(1);
  });

  it('move-up (negative column-count delta) with nothing focused ALSO selects the first item', () => {
    moveFocus(-3);
    expect(selection.focusedId).toBe(1);
  });

  it('Home selects the first item with no prior selection', () => {
    focusHome();
    expect(selection.focusedId).toBe(1);
  });

  it('End selects the last item with no prior selection', () => {
    focusEnd();
    expect(selection.focusedId).toBe(6);
  });

  it('the freshly-focused item is resolvable via focusedItem(), so Enter/f/c/o/e can act on it immediately', () => {
    moveFocus(1);
    expect(focusedItem()?.id).toBe(1);
  });

  it('once something is focused, subsequent moves are relative to it as usual', () => {
    moveFocus(1); // no selection -> item 1 (index 0)
    moveFocus(1); // now relative: index 0 + 1 -> item 2
    expect(selection.focusedId).toBe(2);
  });

  it('is a no-op when there are no results at all', () => {
    results.items = [];
    results.total = 0;
    moveFocus(1);
    expect(selection.focusedId).toBeNull();
  });
});
