<script lang="ts">
  interface Props {
    variant: 'no-items' | 'no-results' | 'scanning';
    onRescan?: () => void;
    onClearFilters?: () => void;
  }
  let { variant, onRescan, onClearFilters }: Props = $props();
</script>

<div class="empty">
  {#if variant === 'scanning'}
    <div class="icon spin">⟳</div>
    <h2>Scanning your library…</h2>
    <p>This can take a moment the first time. New items will appear as they're indexed.</p>
  {:else if variant === 'no-items'}
    <div class="icon">🖼️</div>
    <h2>No media indexed yet</h2>
    <p>Point atomik-meme-web at a folder and run a scan to get started.</p>
    {#if onRescan}
      <button class="btn primary" onclick={onRescan}>Rescan library</button>
    {/if}
  {:else}
    <div class="icon">🔍</div>
    <h2>No results</h2>
    <p>Nothing matches the current search and filters.</p>
    {#if onClearFilters}
      <button class="btn" onclick={onClearFilters}>Clear filters</button>
    {/if}
  {/if}
</div>

<style>
  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    padding: 80px 20px;
    text-align: center;
    color: var(--text-muted);
  }
  .icon {
    font-size: 40px;
    line-height: 1;
  }
  .spin {
    animation: spin 1.4s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  h2 {
    margin: 4px 0 0;
    color: var(--text);
    font-size: 17px;
  }
  p {
    margin: 0;
    max-width: 360px;
    font-size: 13px;
  }
  .btn {
    margin-top: 8px;
  }
</style>
