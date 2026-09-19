<script lang="ts">
  import { api } from '../lib/api';
  import { formatBytes, formatDate } from '../lib/format';
  import { openLightbox, duplicatesState, refreshDuplicates } from '../lib/state.svelte';
  import { openOriginal, revealInFolder } from '../lib/itemActions';
  import type { Item } from '../lib/types';

  // Shared state (not a local copy) so a library switch or a completed rescan can refresh it
  // even while this view isn't mounted to see it happen, and so it's already up to date the
  // next time the user opens it.
  $effect(() => {
    void refreshDuplicates();
  });

  const loading = $derived(duplicatesState.loading);
  const error = $derived(duplicatesState.error);
  const data = $derived(duplicatesState.data);

  async function openAll(items: Item[]): Promise<void> {
    for (const it of items) await openOriginal(it);
  }

  async function revealAll(items: Item[]): Promise<void> {
    for (const it of items) await revealInFolder(it);
  }
</script>

<div class="dup-view">
  {#if loading}
    <p class="muted">Scanning for duplicates…</p>
  {:else if error}
    <p class="muted">{error}</p>
  {:else if !data || data.groups.length === 0}
    <p class="muted">No duplicates found.</p>
  {:else}
    <p class="summary">
      {data.groups.length} group{data.groups.length === 1 ? '' : 's'} · computed {formatDate(data.computed_at)}
    </p>
    {#each data.groups as group, gi (gi)}
      <section class="group">
        <header>
          <span class="badge" class:near={group.kind === 'near'}>
            {group.kind === 'exact' ? 'Exact match' : `Near match · distance ${group.distance}`}
          </span>
          <div class="group-actions">
            <button class="btn small" onclick={() => void openAll(group.items)}>Open both</button>
            <button class="btn small" onclick={() => void revealAll(group.items)}>Reveal</button>
          </div>
        </header>
        <div class="row">
          {#each group.items as item (item.id)}
            <button class="thumb" onclick={() => openLightbox(item.id)}>
              <img src={api.thumbUrl(item.id, 320)} alt={item.name} loading="lazy" decoding="async" />
              <span class="cap">{item.name} · {formatBytes(item.size_bytes)}</span>
            </button>
          {/each}
        </div>
      </section>
    {/each}
  {/if}
</div>

<style>
  .dup-view {
    flex: 1;
    overflow-y: auto;
    padding: 18px;
  }
  .muted {
    color: var(--text-faint);
    text-align: center;
    padding: 60px 0;
  }
  .summary {
    color: var(--text-faint);
    font-size: 12.5px;
    margin: 0 0 14px;
  }
  .group {
    margin-bottom: 22px;
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    background: var(--bg-elevated);
    padding: 12px;
  }
  .group header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 10px;
  }
  .badge {
    font-size: 11.5px;
    padding: 3px 9px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--danger) 18%, transparent);
    color: var(--danger);
  }
  .badge.near {
    background: color-mix(in srgb, var(--warning) 20%, transparent);
    color: var(--warning);
  }
  .group-actions {
    display: flex;
    gap: 6px;
  }
  .btn.small {
    padding: 4px 10px;
    font-size: 12px;
  }
  .row {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
  }
  .thumb {
    width: 160px;
    display: flex;
    flex-direction: column;
    border: none;
    background: none;
    padding: 0;
    text-align: left;
  }
  .thumb img {
    width: 160px;
    height: 160px;
    object-fit: cover;
    border-radius: var(--radius-md);
    background: var(--bg-sunken);
  }
  .cap {
    font-size: 11px;
    color: var(--text-faint);
    margin-top: 4px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
</style>
