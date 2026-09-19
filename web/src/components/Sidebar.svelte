<script lang="ts">
  import {
    collectionsState,
    refreshCollections,
    applyCollection,
    saveCurrentSearchAsCollection,
    renameCollection,
    deleteCollectionById,
    setCategory,
    search,
    viewState,
    settings,
    setSidebarOpen,
    hasActiveFilters,
    categoriesState,
    refreshCategories,
  } from '../lib/state.svelte';
  import type { Collection } from '../lib/types';
  import { resolveIcon } from '../lib/icons';

  let creating = $state(false);
  let newName = $state('');
  let menu = $state<{ collection: Collection; x: number; y: number } | null>(null);

  $effect(() => {
    void refreshCollections();
    void refreshCategories();
  });

  $effect(() => {
    function closeMenu() {
      menu = null;
    }
    window.addEventListener('click', closeMenu);
    return () => window.removeEventListener('click', closeMenu);
  });

  // The fixed set of non-category builtins that belong in the LIBRARY section (the API's
  // "Built-in collections" list, minus the per-category ones). Filtering by an explicit
  // allow-list — rather than by blacklisting an id prefix like "category:" — hides the
  // per-category builtins from LIBRARY (they're already shown in the CATEGORIES section below,
  // sourced from /api/categories) regardless of exactly how the backend formats their ids.
  const LIBRARY_BUILTIN_IDS = new Set([
    'builtin:favorites',
    'builtin:recent-added',
    'builtin:recent-viewed',
    'builtin:images',
    'builtin:gifs',
    'builtin:videos',
    'builtin:nsfw',
    'builtin:duplicates',
  ]);
  const builtins = $derived(collectionsState.items.filter((c) => c.builtin && LIBRARY_BUILTIN_IDS.has(String(c.id))));
  const saved = $derived(collectionsState.items.filter((c) => !c.builtin));

  function isActive(id: string | number): boolean {
    return viewState.activeCollectionId === id;
  }

  function isActiveCategory(name: string): boolean {
    return viewState.activeCollectionId === null && search.category.length === 1 && search.category[0] === name;
  }

  function selectCategory(name: string): void {
    viewState.activeCollectionId = null;
    setCategory([name]);
  }

  async function submitCreate(e: Event): Promise<void> {
    e.preventDefault();
    const name = newName.trim();
    if (!name) return;
    await saveCurrentSearchAsCollection(name);
    newName = '';
    creating = false;
  }

  function openMenu(e: MouseEvent, c: Collection): void {
    e.preventDefault();
    e.stopPropagation();
    menu = { collection: c, x: e.clientX, y: e.clientY };
  }

  async function rename(c: Collection): Promise<void> {
    menu = null;
    const name = window.prompt('Rename collection', c.name);
    if (name && name.trim() && name.trim() !== c.name) {
      await renameCollection(c, name.trim());
    }
  }

  async function remove(c: Collection): Promise<void> {
    menu = null;
    if (window.confirm(`Delete collection "${c.name}"?`)) {
      await deleteCollectionById(c.id);
    }
  }

  function iconFor(c: Collection): string {
    const resolved = resolveIcon(c.icon);
    if (resolved) return resolved;
    const id = String(c.id);
    if (id.includes('favorites')) return '♥';
    if (id.includes('recent-added')) return '◷';
    if (id.includes('recent-viewed')) return '◉';
    if (id.includes('gifs')) return 'GIF';
    if (id.includes('videos')) return '▶';
    if (id.includes('images')) return '▣';
    if (id.includes('nsfw')) return '⚠';
    if (id.includes('duplicates')) return '⧉';
    return '▤';
  }

  /** The resolved icon renders as multi-character text (e.g. "GIF") rather than a single glyph. */
  function isTextIcon(icon: string): boolean {
    return icon.length > 2;
  }
</script>

{#if settings.sidebarOpen}
  <button class="scrim mobile-only" aria-label="Close sidebar" onclick={() => setSidebarOpen(false)}></button>
{/if}

<aside class="sidebar" class:open={settings.sidebarOpen}>
  <nav>
    <section>
      <h3>Library</h3>
      <ul>
        {#each builtins as c (c.id)}
          {@const glyph = iconFor(c)}
          <li>
            <button class="row" class:active={isActive(c.id)} onclick={() => applyCollection(c)}>
              <span class="icon" class:text={isTextIcon(glyph)}>{glyph}</span>
              <span class="label">{c.name}</span>
              {#if c.count !== undefined}<span class="count">{c.count}</span>{/if}
            </button>
          </li>
        {/each}
      </ul>
    </section>

    <section>
      <div class="section-head">
        <h3>Collections</h3>
        <button class="icon-btn small" title="Save current search as collection" onclick={() => (creating = !creating)}>
          +
        </button>
      </div>
      {#if creating}
        <form class="create-form" onsubmit={submitCreate}>
          <input
            type="text"
            placeholder="Collection name"
            bind:value={newName}
            disabled={!hasActiveFilters()}
          />
          <button type="submit" class="btn primary small" disabled={!newName.trim()}>Save</button>
        </form>
        {#if !hasActiveFilters()}
          <p class="hint">Set a search or filter first, then save it as a collection.</p>
        {/if}
      {/if}
      <ul>
        {#each saved as c (c.id)}
          {@const glyph = iconFor(c)}
          <li>
            <button
              class="row"
              class:active={isActive(c.id)}
              onclick={() => applyCollection(c)}
              oncontextmenu={(e) => openMenu(e, c)}
            >
              <span class="icon" class:text={isTextIcon(glyph)}>{glyph}</span>
              <span class="label">{c.name}</span>
            </button>
          </li>
        {:else}
          <li class="empty-hint">No saved collections yet</li>
        {/each}
      </ul>
    </section>

    <section>
      <h3>Categories</h3>
      <ul>
        {#each categoriesState.items as c (c.id)}
          <li>
            <button class="row" class:active={isActiveCategory(c.name)} onclick={() => selectCategory(c.name)}>
              <span class="icon">▤</span>
              <span class="label">{c.name}</span>
              <span class="count">{c.count}</span>
            </button>
          </li>
        {/each}
      </ul>
    </section>
  </nav>
</aside>

{#if menu}
  <div class="context-menu" style:left="{menu.x}px" style:top="{menu.y}px">
    <button onclick={() => rename(menu!.collection)}>Rename</button>
    <button class="danger" onclick={() => remove(menu!.collection)}>Delete</button>
  </div>
{/if}

<style>
  .sidebar {
    width: 220px;
    flex-shrink: 0;
    border-right: 1px solid var(--border);
    background: var(--bg-elevated);
    overflow-y: auto;
    padding: 12px 0;
  }
  .scrim.mobile-only {
    display: none;
  }
  h3 {
    margin: 4px 14px 6px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-faint);
  }
  .section-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-right: 10px;
  }
  ul {
    list-style: none;
    margin: 0 0 10px;
    padding: 0 8px;
  }
  .row {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 8px;
    border-radius: var(--radius-sm);
    background: none;
    border: none;
    color: var(--text-muted);
    font-size: 13px;
    text-align: left;
  }
  .row:hover {
    background: var(--surface-hover);
    color: var(--text);
  }
  .row.active {
    background: color-mix(in srgb, var(--accent) 16%, transparent);
    color: var(--accent);
  }
  .icon {
    width: 18px;
    text-align: center;
    flex-shrink: 0;
  }
  .icon.text {
    font-size: 8.5px;
    font-weight: 700;
    letter-spacing: -0.02em;
  }
  .label {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .count {
    font-size: 11px;
    color: var(--text-faint);
  }
  .icon-btn.small {
    height: 22px;
    min-width: 22px;
    padding: 0;
    font-size: 14px;
  }
  .create-form {
    display: flex;
    gap: 6px;
    padding: 0 14px 8px;
  }
  .create-form input {
    flex: 1;
    min-width: 0;
    height: 26px;
    padding: 0 8px;
    border-radius: var(--radius-sm);
    border: 1px solid var(--border);
    background: var(--bg-sunken);
    font-size: 12px;
  }
  .btn.small {
    padding: 4px 9px;
    font-size: 12px;
  }
  .hint {
    margin: 0 14px 8px;
    font-size: 11px;
    color: var(--text-faint);
  }
  .empty-hint {
    padding: 4px 8px;
    color: var(--text-faint);
    font-size: 12px;
  }
  .context-menu {
    position: fixed;
    z-index: 700;
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    box-shadow: var(--shadow);
    display: flex;
    flex-direction: column;
    padding: 4px;
    min-width: 130px;
  }
  .context-menu button {
    text-align: left;
    padding: 7px 10px;
    background: none;
    border: none;
    border-radius: var(--radius-sm);
    font-size: 13px;
    color: var(--text);
  }
  .context-menu button:hover {
    background: var(--surface-hover);
  }
  .context-menu button.danger {
    color: var(--danger);
  }

  @media (max-width: 860px) {
    .sidebar {
      position: fixed;
      top: 0;
      bottom: 0;
      left: 0;
      z-index: 400;
      transform: translateX(-100%);
      transition: transform var(--transition-med);
      box-shadow: var(--shadow);
    }
    .sidebar.open {
      transform: translateX(0);
    }
    .scrim.mobile-only {
      display: block;
      position: fixed;
      inset: 0;
      background: var(--overlay);
      z-index: 390;
      border: none;
      padding: 0;
    }
  }
</style>
