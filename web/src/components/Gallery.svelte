<script lang="ts">
  import { untrack } from 'svelte';
  import Card from './Card.svelte';
  import EmptyState from './EmptyState.svelte';
  import { appendItems, createLayout, layoutAll, selectVisible, shouldLoadMore } from '../lib/masonry';
  import type { MasonryItemInput, MasonryLayout } from '../lib/masonry';
  import {
    results,
    settings,
    galleryState,
    selection,
    openLightbox,
    loadMore,
    hasActiveFilters,
    clearFilters,
    triggerScan,
    scanState,
  } from '../lib/state.svelte';
  import type { Item } from '../lib/types';

  interface Props {
    onContextMenu: (item: Item, e: MouseEvent) => void;
  }
  let { onContextMenu }: Props = $props();

  const GAP = 14;
  let containerEl: HTMLDivElement | undefined = $state();
  let containerWidth = $state(0);
  let scrollTop = $state(0);
  let viewportHeight = $state(0);
  let layout = $state<MasonryLayout>(createLayout(0, settings.columnMinWidth, GAP));
  // Tracks the results.version this `layout` was built from. NOT reactive on purpose: it's
  // read/written only inside the effect below, purely to detect "a fresh search replaced
  // results.items" vs. "infinite scroll appended to it" — array-length comparisons alone can't
  // tell those apart when two different result sets happen to have the same or a growing
  // length, which previously left stale rects mixed in with new ones (duplicate keys in the
  // {#each} below).
  let layoutVersion = -1;

  function toInput(item: Item): MasonryItemInput {
    const ar = item.width && item.height ? item.width / item.height : 1;
    return { id: item.id, aspectRatio: ar };
  }

  $effect(() => {
    const width = containerWidth;
    const minW = settings.columnMinWidth;
    const items = results.items;
    const version = results.version;
    if (width <= 0) return;
    if (width !== layout.width || minW !== layout.columnMinWidth || version !== layoutVersion) {
      layout = layoutAll(width, minW, GAP, items.map(toInput));
      layoutVersion = version;
    } else if (items.length > layout.rects.length) {
      layout = appendItems(layout, items.slice(layout.rects.length).map(toInput));
    }
    galleryState.columnCount = layout.columnCount;
  });

  $effect(() => {
    if (!containerEl) return;
    const el = containerEl;
    const ro = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      containerWidth = entry.contentRect.width;
      viewportHeight = entry.contentRect.height;
    });
    ro.observe(el);
    return () => ro.disconnect();
  });

  function onScroll(): void {
    if (!containerEl) return;
    scrollTop = containerEl.scrollTop;
    if (shouldLoadMore(layout, scrollTop, viewportHeight)) loadMore();
  }

  // Rebuilt only when the result set changes, not on every scroll event.
  const itemsById = $derived(new Map(results.items.map((it) => [it.id, it] as const)));

  const visibleItems = $derived.by(() => {
    const rects = selectVisible(layout.rects, scrollTop, viewportHeight);
    const byId = itemsById;
    const out: { rect: (typeof rects)[number]; item: Item }[] = [];
    for (const rect of rects) {
      const item = byId.get(rect.id as number);
      if (item) out.push({ rect, item });
    }
    return out;
  });

  // Keep the keyboard-focused card scrolled into view when focus MOVES. Only
  // `selection.focusedId` is tracked: layout / scrollTop / viewportHeight are read via
  // untrack on purpose. Tracking them made this effect re-run on every wheel scroll and on
  // every infinite-scroll append, and whenever the (still) focused card had left the
  // viewport it yanked scrollTop back to it — the "scrolling snaps back / doesn't work"
  // glitch. Clicking a card focuses it, so this hit almost every session.
  $effect(() => {
    const id = selection.focusedId;
    if (id === null) return;
    untrack(() => {
      const el = containerEl;
      if (!el) return;
      const rect = layout.rects.find((r) => r.id === id);
      if (!rect) return;
      if (rect.y < scrollTop) {
        el.scrollTop = Math.max(0, rect.y - GAP);
      } else if (rect.y + rect.h > scrollTop + viewportHeight) {
        el.scrollTop = rect.y + rect.h - viewportHeight + GAP;
      }
    });
  });

  const skeletons = Array.from({ length: 18 }, (_, i) => i);
</script>

<div class="gallery" bind:this={containerEl} onscroll={onScroll}>
  {#if results.loading && results.items.length === 0}
    <div class="skeleton-grid" style:--min-w="{settings.columnMinWidth}px">
      {#each skeletons as i (i)}
        <div class="skeleton-card"></div>
      {/each}
    </div>
  {:else if results.error}
    <div class="error-state">
      <p>{results.error}</p>
    </div>
  {:else if results.items.length === 0}
    {#if hasActiveFilters()}
      <EmptyState variant="no-results" onClearFilters={clearFilters} />
    {:else if scanState.running}
      <EmptyState variant="scanning" />
    {:else}
      <EmptyState variant="no-items" onRescan={() => void triggerScan()} />
    {/if}
  {:else}
    <div class="canvas" style:height="{layout.totalHeight}px">
      {#each visibleItems as v (v.item.id)}
        <Card
          item={v.item}
          rect={v.rect}
          focused={selection.focusedId === v.item.id}
          onOpen={(it) => openLightbox(it.id)}
          {onContextMenu}
        />
      {/each}
    </div>
    {#if results.loadingMore}
      <div class="loading-row" aria-live="polite">
        <span class="spinner"></span> Loading more…
      </div>
    {/if}
  {/if}
</div>

<style>
  .gallery {
    flex: 1;
    /* Flex children default to min-height:auto; be explicit so the gallery can never grow
       past the viewport and hand scrolling to an ancestor that has none. */
    min-height: 0;
    overflow-y: auto;
    overflow-x: hidden;
    position: relative;
    padding: 14px;
  }
  .canvas {
    position: relative;
    width: 100%;
  }
  .skeleton-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(var(--min-w, 220px), 1fr));
    gap: 14px;
  }
  .skeleton-card {
    aspect-ratio: 3 / 4;
    border-radius: var(--radius-md);
    background: linear-gradient(100deg, var(--bg-sunken) 30%, var(--surface-hover) 50%, var(--bg-sunken) 70%);
    background-size: 200% 100%;
    animation: shimmer 1.4s ease-in-out infinite;
  }
  @keyframes shimmer {
    from {
      background-position: 200% 0;
    }
    to {
      background-position: -200% 0;
    }
  }
  .loading-row {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    padding: 18px;
    color: var(--text-faint);
    font-size: 12.5px;
  }
  .spinner {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    border: 2px solid var(--border);
    border-top-color: var(--accent);
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  .error-state {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 60px 20px;
    color: var(--danger);
    text-align: center;
  }
</style>
