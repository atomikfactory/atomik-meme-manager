<script lang="ts">
  import { api } from '../lib/api';
  import { formatBytes, formatDate, formatDuration } from '../lib/format';
  import {
    addTag,
    closeLightbox,
    lightboxIndex,
    lightboxItem,
    lightboxNext,
    lightboxPrev,
    openRandomInLightbox,
    results,
    selection,
    setCategory,
  } from '../lib/state.svelte';
  import { copyMedia, copyPath, openOriginal, revealInFolder, toggleFavorite } from '../lib/itemActions';
  import type { ItemDetail } from '../lib/types';

  const item = $derived(lightboxItem());
  let detail = $state<ItemDetail | null>(null);
  let videoEl: HTMLVideoElement | undefined = $state();

  $effect(() => {
    const current = item;
    detail = null;
    if (!current) return;
    let cancelled = false;
    api
      .item(current.id)
      .then((d) => {
        if (!cancelled) detail = d;
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  });

  // Preload neighbour thumbs so prev/next feels instant.
  $effect(() => {
    const idx = lightboxIndex();
    if (idx === -1) return;
    for (const i of [idx - 1, idx + 1]) {
      const neighbor = results.items[i];
      if (neighbor) {
        const img = new Image();
        img.src = api.thumbUrl(neighbor.id, 320);
      }
    }
  });

  // Stop playback and release the <video> element whenever it is swapped out (the {#key}
  // block recreates it per item) or the lightbox unmounts, so a background video never keeps
  // decoding after it's gone. Capture the element in the effect body: by the time the cleanup
  // runs, `videoEl` is already bound to the *next* item's element, and releasing that one
  // would leave the new video with no source ("No video with supported format...").
  $effect(() => {
    const el = videoEl;
    return () => {
      if (el) {
        el.pause();
        el.removeAttribute('src');
        el.load();
      }
    };
  });

  // ---- Wheel zoom / drag pan for still images (docs: Wheel / dbl-click in ShortcutsHelp) ----
  const MIN_ZOOM = 1;
  const MAX_ZOOM = 8;
  const DBLCLICK_ZOOM = 2.5;
  let stageEl: HTMLDivElement | undefined = $state();
  let imgEl: HTMLImageElement | undefined = $state();
  let zoom = $state(1);
  let panX = $state(0);
  let panY = $state(0);
  let dragging = $state(false);
  let drag = { pointerId: -1, startX: 0, startY: 0, panX: 0, panY: 0, moved: false };

  const zoomable = $derived(item !== null && item.media_type !== 'video');

  function resetZoom(): void {
    zoom = 1;
    panX = 0;
    panY = 0;
  }

  // Every item starts unzoomed.
  $effect(() => {
    void item;
    resetZoom();
  });

  // Never let the scaled image drift so far that its edge crosses the edge of its own
  // unscaled box: at 2x you can reach every corner, at 1x the pan is always 0.
  function clampPan(z: number, x: number, y: number): { x: number; y: number } {
    if (!imgEl || z <= 1) return { x: 0, y: 0 };
    const maxX = (imgEl.offsetWidth * (z - 1)) / 2;
    const maxY = (imgEl.offsetHeight * (z - 1)) / 2;
    return { x: Math.min(maxX, Math.max(-maxX, x)), y: Math.min(maxY, Math.max(-maxY, y)) };
  }

  /** Zoom to `next`, keeping the image point under the screen position (cx, cy) fixed. */
  function zoomTo(next: number, cx: number, cy: number): void {
    if (!imgEl) return;
    const z = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, next));
    if (z === zoom) return;
    // getBoundingClientRect() is post-transform, so its centre is the current visual centre.
    const r = imgEl.getBoundingClientRect();
    const dx = cx - (r.left + r.width / 2);
    const dy = cy - (r.top + r.height / 2);
    const ratio = z / zoom;
    const p = clampPan(z, panX + dx * (1 - ratio), panY + dy * (1 - ratio));
    zoom = z;
    panX = p.x;
    panY = p.y;
  }

  function onWheel(e: WheelEvent): void {
    if (!zoomable || !imgEl) return;
    e.preventDefault();
    // ~16% per notch on a 100px-delta wheel; smooth trackpads give proportionally smaller steps.
    zoomTo(zoom * Math.exp(-e.deltaY * 0.0015), e.clientX, e.clientY);
  }

  // Svelte 5 registers `onwheel` as a passive listener, which forbids preventDefault, so the
  // wheel handler is attached by hand with passive: false.
  $effect(() => {
    const el = stageEl;
    if (!el) return;
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  });

  function onDoubleClick(e: MouseEvent): void {
    if (!zoomable) return;
    if (zoom > 1) resetZoom();
    else zoomTo(DBLCLICK_ZOOM, e.clientX, e.clientY);
  }

  function onPointerDown(e: PointerEvent): void {
    if (!zoomable || zoom <= 1 || e.button !== 0) return;
    e.preventDefault();
    drag = { pointerId: e.pointerId, startX: e.clientX, startY: e.clientY, panX, panY, moved: false };
    dragging = true;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  }

  function onPointerMove(e: PointerEvent): void {
    if (!dragging || e.pointerId !== drag.pointerId) return;
    const p = clampPan(zoom, drag.panX + (e.clientX - drag.startX), drag.panY + (e.clientY - drag.startY));
    panX = p.x;
    panY = p.y;
    drag.moved = true;
  }

  function onPointerUp(e: PointerEvent): void {
    if (!dragging || e.pointerId !== drag.pointerId) return;
    dragging = false;
    (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
  }

  function onBackdropClick(e: MouseEvent): void {
    if (e.target === e.currentTarget) closeLightbox();
  }

  // Lock page scroll while the lightbox is open so the gallery behind it can't scroll, and
  // restore whatever it was on close. This component only exists while the lightbox is open
  // ({#if selection.lightboxId !== null} in App.svelte), so a dependency-free effect that runs
  // once on mount / cleans up on unmount is exactly the right lifecycle.
  $effect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previous;
    };
  });

  function toggleInfo(): void {
    selection.infoOpen = !selection.infoOpen;
  }

  function onTagClick(tag: string): void {
    addTag(tag);
    closeLightbox();
  }

  function onCategoryClick(name: string): void {
    setCategory([name]);
    closeLightbox();
  }
</script>

{#if item}
  {@const current = item}
  <div
    class="lightbox"
    role="dialog"
    aria-modal="true"
    tabindex="-1"
    aria-label={current.title || current.name}
    onclick={onBackdropClick}
  >
    <div class="topbar">
      <div class="filename" title={current.path}>{current.title || current.name}</div>
      <div class="top-actions">
        <button class="icon-btn" title="Toggle info (i)" onclick={toggleInfo}>ⓘ</button>
        <button class="icon-btn" title="Close (Esc)" onclick={closeLightbox}>✕</button>
      </div>
    </div>

    <button class="nav prev" aria-label="Previous" disabled={lightboxIndex() <= 0} onclick={lightboxPrev}>‹</button>
    <button class="nav next" aria-label="Next" onclick={lightboxNext}>›</button>

    <div class="stage" bind:this={stageEl}>
      {#if current.media_type === 'video'}
        {#key current.id}
          <video bind:this={videoEl} src={current.media_url} controls autoplay loop></video>
        {/key}
      {:else}
        <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
        <img
          bind:this={imgEl}
          src={current.media_url}
          alt={current.title || current.name}
          draggable="false"
          class:zoomed={zoom > 1}
          class:dragging
          style:transform="translate({panX}px, {panY}px) scale({zoom})"
          ondblclick={onDoubleClick}
          onpointerdown={onPointerDown}
          onpointermove={onPointerMove}
          onpointerup={onPointerUp}
          onpointercancel={onPointerUp}
        />
        {#if zoom > 1}
          <button class="zoom-badge" title="Reset zoom" onclick={resetZoom}>
            {Math.round(zoom * 100)}%
          </button>
        {/if}
      {/if}
    </div>

    {#if selection.infoOpen}
      <aside class="info">
        <h2>{current.title || current.name}</h2>
        {#if current.description}<p class="desc">{current.description}</p>{/if}

        {#if current.category}
          <button class="tag category" onclick={() => onCategoryClick(current.category!.name)}>
            ▤ {current.category.name}
          </button>
        {/if}
        {#if current.tags.length}
          <div class="tag-list">
            {#each current.tags as tag (tag)}
              <button class="tag" onclick={() => onTagClick(tag)}>#{tag}</button>
            {/each}
          </div>
        {/if}

        {#if detail?.ocr_text}
          <details class="ocr">
            <summary>OCR text</summary>
            <p>{detail.ocr_text}</p>
          </details>
        {/if}

        <dl class="facts">
          {#if current.width && current.height}
            <div><dt>Dimensions</dt><dd>{current.width} × {current.height}</dd></div>
          {/if}
          {#if current.duration_s}
            <div><dt>Duration</dt><dd>{formatDuration(current.duration_s)}</dd></div>
          {/if}
          <div><dt>Size</dt><dd>{formatBytes(current.size_bytes)}</dd></div>
          <div><dt>Created</dt><dd>{formatDate(current.created_at)}</dd></div>
          <div><dt>Modified</dt><dd>{formatDate(current.modified_at)}</dd></div>
          <div><dt>Views</dt><dd>{current.view_count}</dd></div>
          <div class="path-row">
            <dt>Path</dt>
            <dd class="path" title={current.path}>{current.path}</dd>
          </div>
        </dl>

        {#if detail && detail.duplicates.length > 0}
          <p class="dup-note">{detail.duplicates.length} duplicate{detail.duplicates.length === 1 ? '' : 's'} found</p>
        {/if}

        <div class="actions">
          <button class="btn" onclick={() => void toggleFavorite(current)}>
            {current.favorite ? '♥ Unfavorite' : '♡ Favorite'}
          </button>
          <button class="btn" onclick={() => void copyPath(current)}>Copy path</button>
          <button class="btn" onclick={() => void copyMedia(current)}>Copy media</button>
          <button class="btn" onclick={() => void openOriginal(current)}>Open original</button>
          <button class="btn" onclick={() => void revealInFolder(current)}>Reveal in folder</button>
          <a class="btn" href={current.media_url} download={current.name}>Download</a>
          <button class="btn" onclick={() => void openRandomInLightbox()}>🎲 Random</button>
        </div>
      </aside>
    {/if}
  </div>
{/if}

<style>
  .lightbox {
    position: fixed;
    inset: 0;
    z-index: 800;
    /* Near-opaque: the app behind (TopBar wordmark, gallery thumbnails) must not show through. */
    background: var(--lightbox-backdrop);
    display: grid;
    grid-template-columns: 1fr auto;
    grid-template-rows: auto 1fr;
    animation: fade var(--transition-med);
  }
  .topbar {
    grid-column: 1 / -1;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px;
    /* Solid surface, not the translucent backdrop, so it reads as a real toolbar. */
    background: var(--bg-elevated);
    border-bottom: 1px solid var(--border);
    color: var(--text);
  }
  .filename {
    font-size: 13px;
    color: var(--text-muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .top-actions {
    display: flex;
    gap: 4px;
  }
  .top-actions .icon-btn {
    color: var(--text-muted);
  }
  .top-actions .icon-btn:hover {
    background: var(--surface-hover);
    color: var(--text);
  }
  .stage {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    min-width: 0;
    padding: 0 60px 20px;
  }
  .stage img,
  .stage video {
    max-width: 100%;
    max-height: calc(100vh - 90px);
    object-fit: contain;
    border-radius: var(--radius-md);
  }
  .stage img {
    cursor: zoom-in;
    transform-origin: center center;
    transition: transform 90ms ease-out;
    will-change: transform;
    user-select: none;
    -webkit-user-drag: none;
    touch-action: none;
  }
  .stage img.zoomed {
    cursor: grab;
    border-radius: 0;
  }
  .stage img.dragging {
    cursor: grabbing;
    transition: none;
  }
  .zoom-badge {
    position: absolute;
    left: 74px;
    bottom: 30px;
    z-index: 6;
    padding: 4px 10px;
    border: none;
    border-radius: 999px;
    background: rgba(20, 20, 24, 0.7);
    color: white;
    font-size: 12px;
    font-variant-numeric: tabular-nums;
  }
  .zoom-badge:hover {
    background: rgba(20, 20, 24, 0.9);
  }
  .nav {
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    z-index: 5;
    width: 44px;
    height: 44px;
    border-radius: 50%;
    border: none;
    background: rgba(20, 20, 24, 0.5);
    color: white;
    font-size: 24px;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .nav:hover:not(:disabled) {
    background: rgba(20, 20, 24, 0.75);
  }
  .nav:disabled {
    opacity: 0.25;
  }
  .nav.prev {
    left: 14px;
  }
  .nav.next {
    right: 14px;
  }
  .info {
    grid-row: 2;
    grid-column: 2;
    width: 320px;
    max-width: 86vw;
    background: var(--bg-elevated);
    color: var(--text);
    border-left: 1px solid var(--border);
    padding: 16px;
    overflow-y: auto;
  }
  .info h2 {
    margin: 0 0 6px;
    font-size: 16px;
  }
  .desc {
    color: var(--text-muted);
    font-size: 13px;
    margin: 0 0 10px;
  }
  .tag.category {
    display: inline-flex;
    margin-bottom: 8px;
  }
  .tag-list {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-bottom: 10px;
  }
  .tag {
    border: 1px solid var(--border);
    background: var(--surface);
    border-radius: 999px;
    padding: 4px 10px;
    font-size: 12px;
    color: var(--text-muted);
  }
  .tag:hover {
    background: var(--surface-hover);
    color: var(--text);
  }
  .ocr {
    margin-bottom: 10px;
    font-size: 12.5px;
  }
  .ocr p {
    color: var(--text-muted);
    white-space: pre-wrap;
  }
  .facts {
    margin: 10px 0;
    font-size: 12.5px;
  }
  .facts > div {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    padding: 4px 0;
    border-bottom: 1px dashed var(--border);
  }
  .facts dt {
    color: var(--text-faint);
  }
  .facts dd {
    margin: 0;
    text-align: right;
  }
  .path-row {
    flex-direction: column;
    align-items: flex-start;
  }
  .path {
    text-align: left;
    font-family: var(--font-mono);
    font-size: 11px;
    word-break: break-all;
  }
  .dup-note {
    font-size: 12px;
    color: var(--warning);
    margin: 4px 0 10px;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 12px;
  }
  .actions .btn {
    flex: 1 1 calc(50% - 6px);
    justify-content: center;
    font-size: 12.5px;
    text-decoration: none;
  }
  @keyframes fade {
    from {
      opacity: 0;
    }
    to {
      opacity: 1;
    }
  }
  @media (max-width: 720px) {
    .lightbox {
      grid-template-columns: 1fr;
    }
    .info {
      position: fixed;
      inset: auto 0 0 0;
      grid-column: 1;
      width: auto;
      max-width: none;
      max-height: 60vh;
      border-left: none;
      border-top: 1px solid var(--border);
      border-radius: var(--radius-lg) var(--radius-lg) 0 0;
    }
    .stage {
      padding: 0 46px 20px;
    }
  }
</style>
