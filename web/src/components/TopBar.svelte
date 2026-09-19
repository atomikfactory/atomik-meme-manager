<script lang="ts">
  import { api } from '../lib/api';
  import { formatRelative } from '../lib/format';
  import {
    search,
    results,
    setQuery,
    addTag,
    setCategory,
    openLightbox,
    settings,
    cycleTheme,
    scanState,
    triggerScan,
    openRandomInLightbox,
    libraryState,
    openLibraryPanel,
    libraryBasename,
    isNoLibrary,
  } from '../lib/state.svelte';
  import type { Suggestion } from '../lib/types';

  interface Props {
    onHelp: () => void;
  }
  let { onHelp }: Props = $props();

  let inputEl: HTMLInputElement | undefined = $state();
  let queryText = $state(search.q);
  let suggestions = $state<Suggestion[]>([]);
  let showSuggestions = $state(false);
  let activeSuggestion = $state(-1);
  let suggestDebounce: ReturnType<typeof setTimeout> | undefined;

  $effect(() => {
    // keep local input in sync if search.q changes from elsewhere (URL nav, clear filters, …)
    queryText = search.q;
  });

  function onInput(): void {
    setQuery(queryText);
    clearTimeout(suggestDebounce);
    const q = queryText.trim();
    if (q.length < 1) {
      suggestions = [];
      showSuggestions = false;
      return;
    }
    suggestDebounce = setTimeout(async () => {
      try {
        const res = await api.suggest(q, 8);
        suggestions = res.suggestions;
        showSuggestions = suggestions.length > 0;
        activeSuggestion = -1;
      } catch {
        // ignore suggest failures, search itself still works
      }
    }, 150);
  }

  function pickSuggestion(s: Suggestion): void {
    if (s.kind === 'tag') {
      addTag(s.text);
      queryText = '';
      setQuery('');
    } else if (s.kind === 'category') {
      setCategory([s.text]);
      queryText = '';
      setQuery('');
    } else if (s.kind === 'file' && s.item_id !== undefined) {
      openLightbox(s.item_id);
    } else {
      queryText = s.text;
      setQuery(s.text, true);
    }
    showSuggestions = false;
  }

  function onKeydown(e: KeyboardEvent): void {
    if (e.key === 'Escape') {
      // Prevent the browser's native <input type="search"> default action, which clears the
      // field's value on Escape *before* our own handler's logic below gets a say — without
      // this, the first Escape (meant only to close the suggestions dropdown) would also wipe
      // out the query.
      e.preventDefault();
      if (showSuggestions) {
        showSuggestions = false;
      } else if (queryText) {
        queryText = '';
        setQuery('', true);
      } else {
        inputEl?.blur();
      }
      e.stopPropagation();
      return;
    }
    if (!showSuggestions || suggestions.length === 0) return;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      activeSuggestion = (activeSuggestion + 1) % suggestions.length;
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      activeSuggestion = (activeSuggestion - 1 + suggestions.length) % suggestions.length;
    } else if (e.key === 'Enter' && activeSuggestion >= 0) {
      e.preventDefault();
      pickSuggestion(suggestions[activeSuggestion]!);
    }
  }

  function kindIcon(kind: Suggestion['kind']): string {
    switch (kind) {
      case 'tag':
        return '#';
      case 'category':
        return '▤';
      case 'file':
        return '🖼';
      default:
        return '“';
    }
  }

  const themeIcon = $derived(settings.theme === 'dark' ? '🌙' : settings.theme === 'light' ? '☀' : '🖥');
  const scanProgressPct = $derived.by(() => {
    const p = scanState.progress;
    if (!p || !p.total) return null;
    return Math.min(100, Math.round((p.scanned / p.total) * 100));
  });
</script>

<header class="topbar">
  <div class="brand">
    <span class="logo" aria-hidden="true">🐸</span>
    <span class="wordmark">atomik-meme-web</span>
  </div>

  <button
    class="library-chip"
    onclick={openLibraryPanel}
    title={libraryState.info?.library_root ?? 'No library selected'}
  >
    <span class="folder-glyph" aria-hidden="true">📁</span>
    <span class="basename">
      {libraryState.info?.library_root ? libraryBasename(libraryState.info.library_root) : 'No library'}
    </span>
    <span class="chevron" aria-hidden="true">⌄</span>
  </button>

  <div class="search-wrap">
    <input
      id="search-input"
      bind:this={inputEl}
      type="search"
      placeholder="Search titles, tags, OCR text… ( / )"
      autocomplete="off"
      spellcheck="false"
      disabled={isNoLibrary()}
      bind:value={queryText}
      oninput={onInput}
      onkeydown={onKeydown}
      onfocus={() => (showSuggestions = suggestions.length > 0)}
      onblur={() => setTimeout(() => (showSuggestions = false), 120)}
    />
    {#if showSuggestions}
      <ul class="suggestions" role="listbox">
        {#each suggestions as s, i (s.kind + s.text + i)}
          <li>
            <button
              class:active={i === activeSuggestion}
              role="option"
              aria-selected={i === activeSuggestion}
              onmousedown={(e) => e.preventDefault()}
              onclick={() => pickSuggestion(s)}
            >
              <span class="kind">{kindIcon(s.kind)}</span>
              <span class="text">{s.text}</span>
              <span class="tag-kind">{s.kind}</span>
            </button>
          </li>
        {/each}
      </ul>
    {/if}
  </div>

  <div class="result-meta">
    {#if results.loading && results.items.length === 0}
      <span class="muted">Searching…</span>
    {:else}
      <span>{results.total.toLocaleString()} result{results.total === 1 ? '' : 's'}</span>
      <span class="muted">· {formatRelative(results.tookMs)}</span>
      {#if results.fuzzyUsed}
        <span class="fuzzy" title="Query returned few exact hits, showing close matches too">showing close matches</span>
      {/if}
    {/if}
  </div>

  <div class="actions">
    <button class="icon-btn" title="Random (r)" aria-label="Random item" onclick={() => void openRandomInLightbox()}>🎲</button>
    <button
      class="icon-btn scan-btn"
      class:active={scanState.running}
      title="Rescan library"
      aria-label="Rescan library"
      onclick={() => void triggerScan()}
      disabled={scanState.running}
    >
      <span class="scan-icon" class:spin={scanState.running}>⟳</span>
      {#if scanState.running}
        <span class="scan-pct">{scanProgressPct !== null ? `${scanProgressPct}%` : '…'}</span>
      {/if}
    </button>
    <button class="icon-btn" title="Toggle theme (d)" aria-label="Toggle theme" onclick={cycleTheme}>{themeIcon}</button>
    <button class="icon-btn" title="Keyboard shortcuts (?)" aria-label="Keyboard shortcuts" onclick={onHelp}>?</button>
  </div>
</header>

<style>
  .topbar {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 10px 16px;
    border-bottom: 1px solid var(--border);
    background: var(--bg-elevated);
    flex-wrap: wrap;
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 6px;
    font-weight: 700;
    letter-spacing: -0.02em;
    flex-shrink: 0;
  }
  .logo {
    font-size: 18px;
  }
  .wordmark {
    font-size: 15px;
  }
  .library-chip {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    height: 34px;
    padding: 0 10px;
    border-radius: var(--radius-md);
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text-muted);
    font-size: 12.5px;
    flex-shrink: 0;
    max-width: 200px;
  }
  .library-chip:hover {
    background: var(--surface-hover);
    color: var(--text);
  }
  .folder-glyph {
    flex-shrink: 0;
  }
  .basename {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .chevron {
    flex-shrink: 0;
    color: var(--text-faint);
  }
  .search-wrap {
    position: relative;
    flex: 1 1 320px;
    min-width: 180px;
    max-width: 560px;
  }
  input[type='search'] {
    width: 100%;
    height: 36px;
    padding: 0 12px;
    border-radius: var(--radius-md);
    border: 1px solid var(--border);
    background: var(--bg-sunken);
    color: var(--text);
    transition: border-color var(--transition-fast), background var(--transition-fast);
  }
  input[type='search']:disabled {
    opacity: 0.5;
    cursor: default;
  }
  input[type='search']:focus-visible {
    background: var(--bg-elevated);
    border-color: var(--accent);
  }
  input[type='search']::-webkit-search-cancel-button {
    display: none;
  }
  .suggestions {
    position: absolute;
    top: calc(100% + 6px);
    left: 0;
    right: 0;
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    box-shadow: var(--shadow);
    list-style: none;
    margin: 0;
    padding: 4px;
    z-index: 60;
    max-height: 320px;
    overflow-y: auto;
  }
  .suggestions li button {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 7px 8px;
    border-radius: var(--radius-sm);
    background: none;
    border: none;
    text-align: left;
    color: var(--text);
    font-size: 13px;
  }
  .suggestions li button:hover,
  .suggestions li button.active {
    background: var(--surface-hover);
  }
  .kind {
    color: var(--accent);
    width: 16px;
    text-align: center;
    flex-shrink: 0;
  }
  .text {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .tag-kind {
    color: var(--text-faint);
    font-size: 10.5px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .result-meta {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12.5px;
    color: var(--text);
    flex-shrink: 0;
  }
  .muted {
    color: var(--text-faint);
  }
  .fuzzy {
    background: color-mix(in srgb, var(--warning) 20%, transparent);
    color: var(--warning);
    border-radius: 999px;
    padding: 2px 8px;
    font-size: 11px;
  }
  .actions {
    display: flex;
    align-items: center;
    gap: 4px;
    flex-shrink: 0;
    margin-left: auto;
  }
  .scan-btn {
    gap: 4px;
  }
  .scan-icon.spin {
    display: inline-block;
    animation: spin 1s linear infinite;
  }
  .scan-pct {
    font-size: 11px;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  @media (max-width: 720px) {
    .result-meta {
      order: 3;
      width: 100%;
      justify-content: flex-start;
    }
  }
</style>
