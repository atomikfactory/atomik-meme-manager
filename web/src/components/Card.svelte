<script lang="ts">
  import { api } from '../lib/api';
  import { formatDuration } from '../lib/format';
  import { settings, selection } from '../lib/state.svelte';
  import { toggleFavorite } from '../lib/itemActions';
  import type { Item } from '../lib/types';
  import type { MasonryRect } from '../lib/masonry';

  interface Props {
    item: Item;
    rect: MasonryRect;
    focused: boolean;
    onOpen: (item: Item) => void;
    onContextMenu: (item: Item, e: MouseEvent) => void;
  }
  let { item, rect, focused, onOpen, onContextMenu }: Props = $props();

  let hovering = $state(false);
  let videoEl: HTMLVideoElement | undefined = $state();
  let revealed = $state(false); // NSFW blur "click to reveal" toggle

  const isNsfwBlurred = $derived(item.nsfw && settings.nsfwMode === 'blur' && !revealed);
  const thumb = $derived(api.thumbUrl(item.id, 320));

  function onEnter(): void {
    hovering = true;
    if (item.media_type === 'video' && videoEl) {
      if (!videoEl.src) videoEl.src = item.media_url;
      void videoEl.play().catch(() => {});
    }
  }

  function onLeave(): void {
    hovering = false;
    if (item.media_type === 'video' && videoEl) {
      videoEl.pause();
      videoEl.currentTime = 0;
    }
  }

  function onFavoriteClick(e: MouseEvent): void {
    e.stopPropagation();
    void toggleFavorite(item);
  }

  function onRevealClick(e: MouseEvent): void {
    e.stopPropagation();
    revealed = true;
  }

  function onFocusIn(): void {
    selection.focusedId = item.id;
  }
</script>

<!-- A div (not a button) so the favorite/reveal buttons nested inside stay valid HTML. Local
     Enter/Space opens and stops propagation so the app-level shortcut dispatcher doesn't
     also fire for the same keystroke; all other shortcuts (arrows, f, r, …) are handled globally
     via selection.focusedId, kept in sync by onfocus below. -->
<div
  class="card"
  class:focused
  style:left="{rect.x}px"
  style:top="{rect.y}px"
  style:width="{rect.w}px"
  style:height="{rect.h}px"
  role="button"
  tabindex="0"
  onmouseenter={onEnter}
  onmouseleave={onLeave}
  onfocus={onFocusIn}
  onclick={() => onOpen(item)}
  onkeydown={(e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      e.stopPropagation();
      onOpen(item);
    }
  }}
  oncontextmenu={(e) => onContextMenu(item, e)}
  aria-label={item.title || item.name}
>
  <div class="media">
    {#if item.media_type === 'video'}
      <img src={thumb} alt="" loading="lazy" decoding="async" class:hidden={hovering} />
      <video
        bind:this={videoEl}
        muted
        loop
        playsinline
        preload="none"
        class:hidden={!hovering}
        aria-hidden="true"
      ></video>
    {:else if item.media_type === 'gif'}
      <img src={hovering ? item.media_url : thumb} alt="" loading="lazy" decoding="async" />
    {:else}
      <img src={thumb} alt="" loading="lazy" decoding="async" />
    {/if}

    {#if isNsfwBlurred}
      <button class="nsfw-veil" onclick={onRevealClick}>
        <span>NSFW · click to view</span>
      </button>
    {/if}

    <div class="badges">
      {#if item.media_type === 'gif'}
        <span class="badge">GIF</span>
      {:else if item.media_type === 'video'}
        <span class="badge">▶ {formatDuration(item.duration_s)}</span>
      {/if}
    </div>

    <button
      class="favorite"
      class:active={item.favorite}
      aria-label={item.favorite ? 'Remove favorite' : 'Add favorite'}
      onclick={onFavoriteClick}
    >
      {item.favorite ? '♥' : '♡'}
    </button>

    {#if settings.showTitles && (item.title || item.name)}
      <div class="title-reveal" class:visible={hovering}>
        {item.title || item.name}
      </div>
    {/if}
  </div>
</div>

<style>
  .card {
    position: absolute;
    padding: 0;
    border: none;
    background: var(--bg-sunken);
    border-radius: var(--radius-md);
    overflow: hidden;
    cursor: pointer;
    transition: transform var(--transition-fast);
  }
  .card:hover {
    transform: translateY(-2px);
  }
  .card.focused {
    box-shadow: var(--focus-ring);
  }
  .media {
    position: relative;
    width: 100%;
    height: 100%;
  }
  .media img,
  .media video {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }
  .hidden {
    display: none;
  }
  .badges {
    position: absolute;
    top: 6px;
    left: 6px;
    display: flex;
    gap: 4px;
  }
  .badge {
    background: rgba(0, 0, 0, 0.65);
    color: white;
    font-size: 11px;
    padding: 2px 6px;
    border-radius: 5px;
    font-variant-numeric: tabular-nums;
  }
  .favorite {
    position: absolute;
    top: 4px;
    right: 4px;
    width: 26px;
    height: 26px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.45);
    border: none;
    border-radius: 50%;
    color: white;
    font-size: 15px;
    opacity: 0;
    transition: opacity var(--transition-fast), transform var(--transition-fast);
  }
  .card:hover .favorite,
  .favorite.active {
    opacity: 1;
  }
  .favorite.active {
    color: #ff5d7a;
  }
  .favorite:hover {
    transform: scale(1.12);
  }
  .title-reveal {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    padding: 16px 8px 6px;
    background: linear-gradient(to top, rgba(0, 0, 0, 0.75), transparent);
    color: white;
    font-size: 11.5px;
    text-align: left;
    opacity: 0;
    transform: translateY(4px);
    transition: opacity var(--transition-fast), transform var(--transition-fast);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .title-reveal.visible {
    opacity: 1;
    transform: translateY(0);
  }
  .nsfw-veil {
    position: absolute;
    inset: 0;
    border: none;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(20, 20, 24, 0.55);
    backdrop-filter: blur(18px);
    color: white;
    font-size: 11.5px;
    text-align: center;
    padding: 8px;
  }
</style>
