<script lang="ts">
  import { copyMedia, copyPath, openOriginal, revealInFolder, toggleFavorite } from '../lib/itemActions';
  import { openLightbox, searchSimilar, showDuplicates } from '../lib/state.svelte';
  import type { Item } from '../lib/types';

  interface Props {
    item: Item;
    x: number;
    y: number;
    onClose: () => void;
  }
  let { item, x, y, onClose }: Props = $props();

  function run(fn: () => void): void {
    fn();
    onClose();
  }

  let style = $derived.by(() => {
    // Clamp so the menu never renders off-screen.
    const w = 210;
    const h = 260;
    const left = Math.min(x, window.innerWidth - w - 8);
    const top = Math.min(y, window.innerHeight - h - 8);
    return `left:${Math.max(8, left)}px; top:${Math.max(8, top)}px;`;
  });
</script>

<div class="scrim" role="presentation" onclick={onClose} oncontextmenu={(e) => e.preventDefault()}>
  <div class="menu" role="menu" tabindex="-1" style={style} onclick={(e) => e.stopPropagation()}>
    <button onclick={() => run(() => openLightbox(item.id))}>Open viewer</button>
    <button onclick={() => run(() => void toggleFavorite(item))}>
      {item.favorite ? 'Unfavorite' : 'Favorite'}
    </button>
    <hr />
    <button onclick={() => run(() => void copyPath(item))}>Copy path</button>
    <button onclick={() => run(() => void copyMedia(item))}>Copy media</button>
    <hr />
    <button onclick={() => run(() => void openOriginal(item))}>Open original</button>
    <button onclick={() => run(() => void revealInFolder(item))}>Reveal in folder</button>
    <hr />
    <button disabled={item.tags.length === 0} onclick={() => run(() => searchSimilar(item))}>Search similar</button>
    <button onclick={() => run(showDuplicates)}>Find duplicates</button>
  </div>
</div>

<style>
  .scrim {
    position: fixed;
    inset: 0;
    z-index: 650;
  }
  .menu {
    position: fixed;
    display: flex;
    flex-direction: column;
    min-width: 190px;
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    box-shadow: var(--shadow);
    padding: 5px;
    animation: pop var(--transition-fast);
  }
  .menu button {
    text-align: left;
    padding: 7px 10px;
    background: none;
    border: none;
    border-radius: var(--radius-sm);
    font-size: 13px;
    color: var(--text);
  }
  .menu button:hover:not(:disabled) {
    background: var(--surface-hover);
  }
  .menu button:disabled {
    color: var(--text-faint);
    cursor: default;
  }
  .menu hr {
    border: none;
    border-top: 1px solid var(--border);
    margin: 4px 2px;
  }
  @keyframes pop {
    from {
      opacity: 0;
      transform: scale(0.97);
    }
    to {
      opacity: 1;
      transform: scale(1);
    }
  }
</style>
