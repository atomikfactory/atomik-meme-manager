<script lang="ts">
  import {
    search,
    settings,
    toggleType,
    setCategory,
    removeTag,
    setFavoriteOnly,
    setSort,
    setNsfwMode,
    setColumnMinWidth,
    categoriesState,
    refreshCategories,
  } from '../lib/state.svelte';
  import type { MediaType, NsfwUiMode, SortKey } from '../lib/types';

  $effect(() => {
    void refreshCategories();
  });

  const TYPES: { key: MediaType; label: string }[] = [
    { key: 'image', label: 'Image' },
    { key: 'gif', label: 'GIF' },
    { key: 'video', label: 'Video' },
  ];

  const NSFW_CYCLE: NsfwUiMode[] = ['hide', 'blur', 'show'];
  const NSFW_LABEL: Record<NsfwUiMode, string> = { hide: 'Hide', blur: 'Blur', show: 'Show' };

  const SORTS: { key: SortKey; label: string }[] = [
    { key: 'newest', label: 'Newest' },
    { key: 'oldest', label: 'Oldest' },
    { key: 'name', label: 'Name' },
    { key: 'size', label: 'Size' },
    { key: 'duration', label: 'Duration' },
    { key: 'random', label: 'Random' },
  ];
  const sortOptions = $derived(search.q ? [{ key: 'relevance' as SortKey, label: 'Relevance' }, ...SORTS] : SORTS);

  function onCategoryChange(e: Event): void {
    const value = (e.target as HTMLSelectElement).value;
    setCategory(value ? [value] : []);
  }

  function onSortChange(e: Event): void {
    setSort((e.target as HTMLSelectElement).value as SortKey);
  }
</script>

<div class="filterbar">
  <div class="group types">
    {#each TYPES as t (t.key)}
      <button class="chip" class:active={search.type.includes(t.key)} onclick={() => toggleType(t.key)}>
        {t.label}
      </button>
    {/each}
    <button class="chip" class:active={search.favorite} onclick={() => setFavoriteOnly(!search.favorite)}>
      ♥ Favorites
    </button>
  </div>

  <div class="group">
    <div class="nsfw-toggle" role="radiogroup" aria-label="NSFW visibility">
      {#each NSFW_CYCLE as mode (mode)}
        <button
          class="seg"
          class:active={settings.nsfwMode === mode}
          role="radio"
          aria-checked={settings.nsfwMode === mode}
          onclick={() => setNsfwMode(mode)}
        >
          {NSFW_LABEL[mode]}
        </button>
      {/each}
    </div>
  </div>

  <div class="group">
    <select class="select" aria-label="Category" value={search.category[0] ?? ''} onchange={onCategoryChange}>
      <option value="">All categories</option>
      {#each categoriesState.items as c (c.id)}
        <option value={c.name}>{c.name} ({c.count})</option>
      {/each}
    </select>
  </div>

  {#if search.tag.length > 0}
    <div class="group tags">
      {#each search.tag as tag (tag)}
        <button class="chip active" onclick={() => removeTag(tag)}>
          {tag} <span class="x">×</span>
        </button>
      {/each}
    </div>
  {/if}

  <div class="group spacer"></div>

  <div class="group">
    <select class="select" aria-label="Sort" value={search.sort} onchange={onSortChange}>
      {#each sortOptions as s (s.key)}
        <option value={s.key}>{s.label}</option>
      {/each}
    </select>
  </div>

  <div class="group column-size">
    <label for="column-size-range">Size</label>
    <input
      id="column-size-range"
      type="range"
      min="140"
      max="420"
      step="10"
      value={settings.columnMinWidth}
      oninput={(e) => setColumnMinWidth(Number((e.target as HTMLInputElement).value))}
    />
  </div>
</div>

<style>
  .filterbar {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 16px;
    border-bottom: 1px solid var(--border);
    background: var(--bg);
    overflow-x: auto;
    scrollbar-width: none;
  }
  .filterbar::-webkit-scrollbar {
    display: none;
  }
  .group {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }
  .spacer {
    flex: 1;
    min-width: 8px;
  }
  .tags {
    max-width: 320px;
    overflow-x: auto;
  }
  .x {
    opacity: 0.7;
  }
  .nsfw-toggle {
    display: flex;
    border: 1px solid var(--border);
    border-radius: 999px;
    overflow: hidden;
  }
  .seg {
    padding: 5px 12px;
    background: var(--surface);
    color: var(--text-muted);
    border: none;
    font-size: 12px;
    border-right: 1px solid var(--border);
  }
  .seg:last-child {
    border-right: none;
  }
  .seg.active {
    background: var(--accent);
    color: white;
  }
  .select {
    height: 30px;
    padding: 0 8px;
    border-radius: var(--radius-md);
    border: 1px solid var(--border);
    background: var(--surface);
    font-size: 12.5px;
  }
  .column-size {
    gap: 8px;
  }
  .column-size label {
    font-size: 11.5px;
    color: var(--text-faint);
  }
  .column-size input[type='range'] {
    width: 90px;
    accent-color: var(--accent);
  }
</style>
