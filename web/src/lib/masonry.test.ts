import { describe, expect, it } from 'vitest';
import {
  appendItems,
  computeColumnCount,
  createLayout,
  layoutAll,
  selectVisible,
  shouldLoadMore,
  type MasonryItemInput,
} from './masonry';

function items(n: number, aspectRatio = 1): MasonryItemInput[] {
  return Array.from({ length: n }, (_, i) => ({ id: i, aspectRatio }));
}

describe('computeColumnCount', () => {
  it('fits as many columns of at least columnMinWidth as possible', () => {
    // width 1000, min 240, gap 16 -> floor((1000+16)/(240+16)) = floor(1016/256) = 3
    expect(computeColumnCount(1000, 240, 16)).toBe(3);
  });

  it('never returns fewer than 1 column', () => {
    expect(computeColumnCount(100, 500, 16)).toBe(1);
    expect(computeColumnCount(0, 240, 16)).toBe(1);
  });

  it('grows with container width', () => {
    const narrow = computeColumnCount(400, 200, 8);
    const wide = computeColumnCount(1600, 200, 8);
    expect(wide).toBeGreaterThan(narrow);
  });
});

describe('appendItems shortest-column placement', () => {
  it('places items into the currently shortest column', () => {
    // 2 columns, all square (aspect 1) so column width == item height.
    const layout = layoutAll(400, 180, 20, items(4));
    // columnWidth = (400 - 20*1)/2 = 190, height per square item = 190
    expect(layout.columnCount).toBe(2);
    const colOf = (id: number) => layout.rects.find((r) => r.id === id)?.column;
    expect(colOf(0)).toBe(0);
    expect(colOf(1)).toBe(1);
    // both columns now equal height, ties broken toward column 0
    expect(colOf(2)).toBe(0);
    expect(colOf(3)).toBe(1);
  });

  it('balances columns of differing item heights', () => {
    // item 0 is very tall (aspect ratio 0.1 -> height = width/0.1 = 10x width)
    const layout = layoutAll(400, 180, 20, [
      { id: 'tall', aspectRatio: 0.1 },
      { id: 'a', aspectRatio: 1 },
      { id: 'b', aspectRatio: 1 },
    ]);
    const col = (id: string) => layout.rects.find((r) => r.id === id)?.column;
    expect(col('tall')).toBe(0);
    // both subsequent square items should go to column 1 (still shorter than column 0)
    expect(col('a')).toBe(1);
    expect(col('b')).toBe(1);
  });

  it('falls back to aspect ratio 1 for invalid input', () => {
    const layout = layoutAll(400, 180, 20, [{ id: 'x', aspectRatio: 0 }]);
    const rect = layout.rects[0]!;
    expect(rect.h).toBeCloseTo(rect.w, 5);
  });
});

describe('incremental append stability', () => {
  it('never moves or mutates earlier items when more are appended', () => {
    const base = createLayout(1000, 240, 16);
    const first = appendItems(base, items(6));
    const firstRectsSnapshot = first.rects.map((r) => ({ ...r }));

    const second = appendItems(first, items(6).map((it) => ({ ...it, id: `p2-${it.id}` })));

    // Earlier rects are the same objects, unchanged, and in the same order.
    for (let i = 0; i < firstRectsSnapshot.length; i++) {
      expect(second.rects[i]).toEqual(firstRectsSnapshot[i]);
      expect(second.rects[i]).toBe(first.rects[i]); // same reference, not recomputed
    }
    expect(second.rects.length).toBe(12);
    // total height only grows or stays the same
    expect(second.totalHeight).toBeGreaterThanOrEqual(first.totalHeight);
  });

  it('does not mutate the layout passed in', () => {
    const base = createLayout(1000, 240, 16);
    const before = JSON.stringify(base);
    appendItems(base, items(3));
    expect(JSON.stringify(base)).toBe(before);
  });

  it('a full relayout at a new width redistributes everything (unlike append)', () => {
    const narrow = layoutAll(400, 180, 20, items(6));
    const wide = layoutAll(1200, 180, 20, items(6));
    expect(wide.columnCount).toBeGreaterThan(narrow.columnCount);
  });
});

describe('selectVisible', () => {
  it('selects only rects intersecting the render window', () => {
    // 1 column, 10 square items of height 100 stacked with no gap.
    const layout = layoutAll(100, 100, 0, items(10, 1));
    // viewport height 100, scrollTop 500 -> window is [500-150, 500+250] = [350, 750]
    const visible = selectVisible(layout.rects, 500, 100);
    const ids = visible.map((r) => r.id).sort((a, b) => Number(a) - Number(b));
    // items are 0..9 at y = 0,100,...,900 each 100 tall -> intersects [350,750]: items 3..7 (y+h>=350, y<=750)
    expect(ids).toEqual([3, 4, 5, 6, 7]);
  });

  it('returns an empty array when nothing intersects', () => {
    const layout = layoutAll(100, 100, 0, items(3, 1));
    expect(selectVisible(layout.rects, 100000, 50)).toEqual([]);
  });
});

describe('shouldLoadMore', () => {
  it('is true once scroll position nears the bottom of the content', () => {
    const layout = layoutAll(100, 100, 0, items(5, 1)); // totalHeight = 500
    expect(shouldLoadMore(layout, 0, 100, 2)).toBe(false); // 0 + 300 < 500
    expect(shouldLoadMore(layout, 250, 100, 2)).toBe(true); // 250 + 300 >= 500
  });
});
