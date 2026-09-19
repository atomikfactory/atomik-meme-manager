<script lang="ts">
  // Shared "pick a library" form — the body of the LibraryPanel modal, and also rendered
  // inline (no modal chrome) by the full-screen empty state when there is no library yet.
  import {
    beginSwitch,
    libraryState,
    pickLibraryFolder,
    browseFs,
    removeRecentLibrary,
    switchToRecent,
  } from '../lib/state.svelte';
  import { formatDate } from '../lib/format';

  interface Props {
    inline?: boolean;
  }
  let { inline = false }: Props = $props();

  let typedPath = $state('');
  let browsing = $state(false);

  function toggleBrowse(): void {
    browsing = !browsing;
    if (browsing && libraryState.browse.path === null && libraryState.browse.roots.length === 0) {
      void browseFs();
    }
  }

  function breadcrumbSegments(p: string): { label: string; path: string }[] {
    const isDrive = /^[A-Za-z]:\\?$/.test(p);
    if (isDrive) return [{ label: p, path: p }];
    const parts = p.split('\\').filter(Boolean);
    const segments: { label: string; path: string }[] = [];
    let acc = '';
    for (let i = 0; i < parts.length; i++) {
      acc = i === 0 ? `${parts[0]}\\` : `${acc}${parts[i]}\\`;
      segments.push({ label: parts[i]!, path: i === parts.length - 1 ? acc.replace(/\\$/, '') || acc : acc.replace(/\\$/, '') });
    }
    // First segment is the drive root; keep its trailing backslash.
    if (segments.length) segments[0] = { ...segments[0]!, path: `${parts[0]}\\` };
    return segments;
  }

  function submitTypedPath(e: Event): void {
    e.preventDefault();
    const p = typedPath.trim();
    if (p) void beginSwitch(p);
  }

  function useThisFolder(): void {
    if (libraryState.browse.path) void beginSwitch(libraryState.browse.path);
  }
</script>

<div class="chooser" class:inline>
  {#if libraryState.error}
    <p class="error" role="alert">{libraryState.error}</p>
  {/if}

  <section class="row">
    <button
      class="btn primary"
      disabled={libraryState.info?.native_picker === false || libraryState.phase === 'picking'}
      onclick={() => void pickLibraryFolder()}
    >
      {libraryState.phase === 'picking' ? 'Waiting for dialog…' : 'Choose folder…'}
    </button>
    {#if libraryState.info?.native_picker === false}
      <p class="hint">Native dialog unavailable — use Browse or type a path below.</p>
    {/if}
  </section>

  <section class="row">
    <button class="btn" onclick={toggleBrowse} aria-expanded={browsing}>
      {browsing ? 'Hide browser' : 'Browse…'}
    </button>
    {#if browsing}
      <div class="browse">
        {#if libraryState.browse.path}
          <nav class="breadcrumb" aria-label="Current folder">
            <button class="crumb" onclick={() => void browseFs()}>Computer</button>
            {#each breadcrumbSegments(libraryState.browse.path) as seg (seg.path)}
              <span class="sep">/</span>
              <button class="crumb" onclick={() => void browseFs(seg.path)}>{seg.label}</button>
            {/each}
          </nav>
        {:else}
          <p class="breadcrumb-root">Computer</p>
        {/if}

        {#if libraryState.browse.loading}
          <p class="hint">Loading…</p>
        {:else if libraryState.browse.error}
          <p class="error">{libraryState.browse.error}</p>
        {:else}
          <ul class="tree" role="listbox" aria-label="Folders">
            {#if libraryState.browse.path === null}
              {#each libraryState.browse.roots as root (root.path)}
                <li>
                  <button class="entry" onclick={() => void browseFs(root.path)}>📁 {root.name}</button>
                </li>
              {/each}
            {:else}
              {#each libraryState.browse.entries as entry (entry.path)}
                <li>
                  <button class="entry" onclick={() => void browseFs(entry.path)}>
                    📁 {entry.name}
                  </button>
                </li>
              {:else}
                <li class="empty">No subfolders</li>
              {/each}
            {/if}
          </ul>
        {/if}

        <div class="browse-actions">
          <button class="btn primary small" disabled={!libraryState.browse.path} onclick={useThisFolder}>
            Use this folder
          </button>
        </div>
      </div>
    {/if}
  </section>

  <section class="row">
    <form class="typed-path" onsubmit={submitTypedPath}>
      <input
        type="text"
        placeholder="D:\my-memes"
        bind:value={typedPath}
        aria-label="Library folder path"
        onkeydown={(e) => {
          // Belt-and-braces: native implicit-submit-on-Enter can be skipped by some
          // browsers/automation when the form's only submit button is (or was a tick ago)
          // disabled, so handle Enter directly rather than relying on it alone.
          if (e.key === 'Enter') submitTypedPath(e);
        }}
      />
      <button type="submit" class="btn" disabled={!typedPath.trim()}>Switch</button>
    </form>
  </section>

  {#if libraryState.info?.recent.length}
    <section class="row">
      <h3>Recent libraries</h3>
      <ul class="recent">
        {#each libraryState.info.recent as r (r.path)}
          <li class:missing={!r.exists}>
            <button class="recent-path" disabled={!r.exists} onclick={() => void switchToRecent(r.path)} title={r.path}>
              <span class="path-text">{r.path}</span>
              <span class="meta">
                {r.exists ? formatDate(r.last_opened) : 'not found'}
              </span>
            </button>
            <button class="remove" aria-label="Remove from recent" onclick={() => void removeRecentLibrary(r.path)}>×</button>
          </li>
        {/each}
      </ul>
    </section>
  {/if}
</div>

<style>
  .chooser {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .chooser.inline {
    max-width: 480px;
    margin: 0 auto;
  }
  .row {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  h3 {
    margin: 0 0 4px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-faint);
  }
  .error {
    margin: 0;
    padding: 8px 10px;
    border-radius: var(--radius-sm);
    background: color-mix(in srgb, var(--danger) 14%, transparent);
    color: var(--danger);
    font-size: 12.5px;
  }
  .hint {
    margin: 0;
    font-size: 11.5px;
    color: var(--text-faint);
  }
  .browse {
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    padding: 10px;
    background: var(--bg-sunken);
  }
  .breadcrumb,
  .breadcrumb-root {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 2px;
    margin: 0 0 8px;
    font-size: 12px;
    color: var(--text-muted);
  }
  .crumb {
    background: none;
    border: none;
    color: var(--text-muted);
    padding: 2px 4px;
    border-radius: 4px;
  }
  .crumb:hover {
    background: var(--surface-hover);
    color: var(--text);
  }
  .sep {
    color: var(--text-faint);
  }
  .tree {
    list-style: none;
    margin: 0;
    padding: 0;
    max-height: 220px;
    overflow-y: auto;
  }
  .entry {
    width: 100%;
    text-align: left;
    padding: 6px 8px;
    border-radius: var(--radius-sm);
    background: none;
    border: none;
    font-size: 13px;
    color: var(--text);
  }
  .entry:hover {
    background: var(--surface-hover);
  }
  .empty {
    padding: 6px 8px;
    color: var(--text-faint);
    font-size: 12.5px;
  }
  .browse-actions {
    margin-top: 8px;
    display: flex;
    justify-content: flex-end;
  }
  .typed-path {
    display: flex;
    gap: 8px;
  }
  .typed-path input {
    flex: 1;
    min-width: 0;
    height: 34px;
    padding: 0 10px;
    border-radius: var(--radius-md);
    border: 1px solid var(--border);
    background: var(--bg-sunken);
    color: var(--text);
    font-family: var(--font-mono);
    font-size: 12.5px;
  }
  .recent {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .recent li {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .recent-path {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 7px 10px;
    border-radius: var(--radius-sm);
    background: var(--surface);
    border: 1px solid var(--border);
    text-align: left;
  }
  .recent-path:hover:not(:disabled) {
    background: var(--surface-hover);
  }
  .recent-path:disabled {
    opacity: 0.5;
    cursor: default;
  }
  .path-text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-family: var(--font-mono);
    font-size: 12px;
  }
  .meta {
    flex-shrink: 0;
    font-size: 11px;
    color: var(--text-faint);
  }
  li.missing .meta {
    color: var(--danger);
  }
  .remove {
    flex-shrink: 0;
    width: 26px;
    height: 26px;
    border-radius: 50%;
    background: none;
    border: none;
    color: var(--text-faint);
    font-size: 16px;
  }
  .remove:hover {
    background: var(--surface-hover);
    color: var(--danger);
  }
  .btn.small {
    padding: 4px 10px;
    font-size: 12px;
  }
</style>
