<script lang="ts">
  import { statsState } from '../lib/state.svelte';
  import { formatBytes, formatDate } from '../lib/format';
</script>

<footer class="stats-bar">
  {#if statsState.data}
    {@const s = statsState.data}
    <span>{s.total.toLocaleString()} items</span>
    <span class="sep">·</span>
    <span>{s.by_type.image.toLocaleString()} images</span>
    <span class="sep">·</span>
    <span>{s.by_type.gif.toLocaleString()} gifs</span>
    <span class="sep">·</span>
    <span>{s.by_type.video.toLocaleString()} videos</span>
    <span class="sep">·</span>
    <span>{s.favorites.toLocaleString()} favorites</span>
    <span class="sep">·</span>
    <span>{formatBytes(s.total_bytes)}</span>
    {#if s.last_scan?.finished_at}
      <span class="sep">·</span>
      <span>Last scan {formatDate(s.last_scan.finished_at)}</span>
    {/if}
  {:else}
    <span class="muted">Loading stats…</span>
  {/if}
</footer>

<style>
  .stats-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 16px;
    border-top: 1px solid var(--border);
    background: var(--bg-elevated);
    color: var(--text-faint);
    font-size: 11.5px;
    overflow-x: auto;
    white-space: nowrap;
  }
  .sep {
    opacity: 0.5;
  }
  .muted {
    color: var(--text-faint);
  }
</style>
