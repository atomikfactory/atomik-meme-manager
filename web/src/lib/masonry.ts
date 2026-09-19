// Pure, framework-free masonry layout math. Unit-tested in masonry.test.ts.

export interface MasonryItemInput {
  id: number | string;
  /** width / height. Use 1 when unknown. */
  aspectRatio: number;
}

export interface MasonryRect {
  id: number | string;
  x: number;
  y: number;
  w: number;
  h: number;
  column: number;
}

export interface MasonryLayout {
  width: number;
  columnMinWidth: number;
  gap: number;
  columnCount: number;
  columnWidth: number;
  /** Current bottom edge (including trailing gap) of each column. */
  columnHeights: number[];
  rects: MasonryRect[];
  totalHeight: number;
}

/** Column count = max(1, floor((width + gap) / (columnMinWidth + gap))). */
export function computeColumnCount(width: number, columnMinWidth: number, gap: number): number {
  if (width <= 0 || columnMinWidth <= 0) return 1;
  return Math.max(1, Math.floor((width + gap) / (columnMinWidth + gap)));
}

/** Start a fresh, empty layout for a given container width. Call again (discarding the old
 * layout) whenever width or columnMinWidth changes; otherwise reuse via appendItems. */
export function createLayout(width: number, columnMinWidth: number, gap: number): MasonryLayout {
  const columnCount = computeColumnCount(width, columnMinWidth, gap);
  const columnWidth = columnCount > 0 ? (width - gap * (columnCount - 1)) / columnCount : width;
  return {
    width,
    columnMinWidth,
    gap,
    columnCount,
    columnWidth: Math.max(0, columnWidth),
    columnHeights: new Array(columnCount).fill(0),
    rects: [],
    totalHeight: 0,
  };
}

function shortestColumn(heights: number[]): number {
  let col = 0;
  for (let i = 1; i < heights.length; i++) {
    const h = heights[i];
    if (h !== undefined && h < (heights[col] ?? 0)) col = i;
  }
  return col;
}

/**
 * Place new items into the shortest column, in order. Returns a NEW layout object; the
 * `rects` array for previously-placed items is preserved (same objects, same order, same
 * positions) — appending never relayouts earlier items. Only call this again with a layout
 * from `createLayout` (or a previous `appendItems` result) of the SAME width/columnCount.
 */
export function appendItems(layout: MasonryLayout, items: MasonryItemInput[]): MasonryLayout {
  if (items.length === 0) return layout;
  const columnHeights = layout.columnHeights.slice();
  const newRects: MasonryRect[] = [];
  for (const item of items) {
    const col = shortestColumn(columnHeights);
    const ar = item.aspectRatio > 0 && Number.isFinite(item.aspectRatio) ? item.aspectRatio : 1;
    const w = layout.columnWidth;
    const h = w / ar;
    const x = col * (layout.columnWidth + layout.gap);
    const y = columnHeights[col] ?? 0;
    newRects.push({ id: item.id, x, y, w, h, column: col });
    columnHeights[col] = y + h + layout.gap;
  }
  const totalHeight = Math.max(0, ...columnHeights.map((h) => Math.max(0, h - layout.gap)));
  return {
    ...layout,
    columnHeights,
    rects: layout.rects.concat(newRects),
    totalHeight,
  };
}

/** Full (re)layout from scratch — equivalent to createLayout + appendItems(all). */
export function layoutAll(
  width: number,
  columnMinWidth: number,
  gap: number,
  items: MasonryItemInput[]
): MasonryLayout {
  return appendItems(createLayout(width, columnMinWidth, gap), items);
}

/**
 * Select rects that intersect the render window
 * [scrollTop - 1.5*viewport, scrollTop + 2.5*viewport].
 */
export function selectVisible(
  rects: readonly MasonryRect[],
  scrollTop: number,
  viewportHeight: number
): MasonryRect[] {
  const top = scrollTop - 1.5 * viewportHeight;
  const bottom = scrollTop + 2.5 * viewportHeight;
  return rects.filter((r) => r.y + r.h >= top && r.y <= bottom);
}

/** True when the last placed row is within `factor` viewports of the bottom — used to decide
 * when to fetch the next page for infinite scroll. */
export function shouldLoadMore(
  layout: MasonryLayout,
  scrollTop: number,
  viewportHeight: number,
  factor = 2
): boolean {
  return scrollTop + viewportHeight * (1 + factor) >= layout.totalHeight;
}
