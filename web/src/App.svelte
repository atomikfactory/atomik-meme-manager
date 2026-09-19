<script lang="ts">
  import TopBar from './components/TopBar.svelte';
  import FilterBar from './components/FilterBar.svelte';
  import Sidebar from './components/Sidebar.svelte';
  import Gallery from './components/Gallery.svelte';
  import DuplicatesView from './components/DuplicatesView.svelte';
  import Lightbox from './components/Lightbox.svelte';
  import ContextMenu from './components/ContextMenu.svelte';
  import Toasts from './components/Toasts.svelte';
  import DropZone from './components/DropZone.svelte';
  import StatsBar from './components/StatsBar.svelte';
  import ShortcutsHelp from './components/ShortcutsHelp.svelte';
  import LibraryPanel from './components/LibraryPanel.svelte';
  import LibraryChooser from './components/LibraryChooser.svelte';
  import { matchShortcut, type ShortcutAction } from './lib/shortcuts';
  import { api } from './lib/api';
  import {
    loadFromUrl,
    runSearch,
    refreshCollections,
    refreshStats,
    refreshScanStatus,
    selection,
    closeLightbox,
    moveFocus,
    focusHome,
    focusEnd,
    focusedItem,
    lightboxItem,
    openLightbox,
    lightboxNext,
    lightboxPrev,
    openRandomInLightbox,
    cycleNsfwMode,
    cycleTheme,
    bumpColumnSize,
    clearFilters,
    toggleType,
    galleryState,
    toggleSidebar,
    viewState,
    showGallery,
    libraryState,
    openLibraryPanel,
    closeLibraryPanel,
    refreshLibraryInfo,
    isNoLibrary,
  } from './lib/state.svelte';
  import { copyMedia, copyPath, openOriginal, revealInFolder, toggleFavorite } from './lib/itemActions';
  import type { ConfigResponse, Item } from './lib/types';

  let showHelp = $state(false);
  let config = $state<ConfigResponse | null>(null);
  let contextMenu = $state<{ item: Item; x: number; y: number } | null>(null);

  function openContextMenu(item: Item, e: MouseEvent): void {
    e.preventDefault();
    contextMenu = { item, x: e.clientX, y: e.clientY };
  }

  // One-time initial load. Deliberately NOT inside $effect: loadFromUrl()/runSearch() both
  // read and write the shared `search` state (via nsfwParam/results), and doing that inside a
  // Svelte 5 $effect makes the effect depend on the very values it just wrote, which
  // re-triggers itself forever (effect_update_depth_exceeded). Plain script-top-level code
  // runs exactly once when this root component is created, which is all we need here.
  loadFromUrl();
  void runSearch(true);
  void refreshCollections();
  void refreshStats();
  void refreshScanStatus();
  void refreshLibraryInfo();
  api
    .config()
    .then((c) => (config = c))
    .catch(() => {});

  $effect(() => {
    function onPopState(): void {
      loadFromUrl();
      void runSearch(true);
    }
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  });

  function focusSearch(): void {
    document.getElementById('search-input')?.focus();
  }

  function handleGridAction(action: ShortcutAction, e: KeyboardEvent): void {
    switch (action) {
      case 'focus-search':
        e.preventDefault();
        focusSearch();
        break;
      case 'move-left':
        e.preventDefault();
        moveFocus(-1);
        break;
      case 'move-right':
        e.preventDefault();
        moveFocus(1);
        break;
      case 'move-up':
        e.preventDefault();
        moveFocus(-galleryState.columnCount);
        break;
      case 'move-down':
        e.preventDefault();
        moveFocus(galleryState.columnCount);
        break;
      case 'go-home':
        e.preventDefault();
        focusHome();
        break;
      case 'go-end':
        e.preventDefault();
        focusEnd();
        break;
      case 'open': {
        const it = focusedItem();
        if (it) openLightbox(it.id);
        break;
      }
      case 'favorite': {
        const it = focusedItem();
        if (it) void toggleFavorite(it);
        break;
      }
      case 'random':
        void openRandomInLightbox();
        break;
      case 'copy-path': {
        const it = focusedItem();
        if (it) void copyPath(it);
        break;
      }
      case 'copy-media': {
        const it = focusedItem();
        if (it) void copyMedia(it);
        break;
      }
      case 'open-original': {
        const it = focusedItem();
        if (it) void openOriginal(it);
        break;
      }
      case 'reveal': {
        const it = focusedItem();
        if (it) void revealInFolder(it);
        break;
      }
      case 'filter-image':
        toggleType('image');
        break;
      case 'filter-gif':
        toggleType('gif');
        break;
      case 'filter-video':
        toggleType('video');
        break;
      case 'clear-filters':
        clearFilters();
        break;
      case 'cycle-nsfw':
        cycleNsfwMode();
        break;
      case 'toggle-theme':
        cycleTheme();
        break;
      case 'column-size-up':
        bumpColumnSize(20);
        break;
      case 'column-size-down':
        bumpColumnSize(-20);
        break;
      default:
        break;
    }
  }

  function handleLightboxAction(action: ShortcutAction, e: KeyboardEvent): void {
    const it = lightboxItem();
    switch (action) {
      case 'close-or-clear':
        closeLightbox();
        break;
      case 'move-left':
        e.preventDefault();
        lightboxPrev();
        break;
      case 'move-right':
        e.preventDefault();
        lightboxNext();
        break;
      case 'toggle-info':
        selection.infoOpen = !selection.infoOpen;
        break;
      case 'favorite':
        if (it) void toggleFavorite(it);
        break;
      case 'random':
        void openRandomInLightbox();
        break;
      case 'copy-path':
        if (it) void copyPath(it);
        break;
      case 'copy-media':
        if (it) void copyMedia(it);
        break;
      case 'open-original':
        if (it) void openOriginal(it);
        break;
      case 'reveal':
        if (it) void revealInFolder(it);
        break;
      default:
        break;
    }
  }

  $effect(() => {
    function onKeydown(e: KeyboardEvent): void {
      const action = matchShortcut(e);
      if (!action) return;
      if (action === 'help') {
        e.preventDefault();
        showHelp = !showHelp;
        return;
      }
      if (action === 'open-library') {
        e.preventDefault();
        if (libraryState.panelOpen) closeLibraryPanel();
        else openLibraryPanel();
        return;
      }
      if (showHelp) {
        if (action === 'close-or-clear') showHelp = false;
        return;
      }
      if (libraryState.panelOpen) {
        // closeLibraryPanel() is itself a no-op while a switch's scan is in progress, so Esc
        // is safe to forward unconditionally here.
        if (action === 'close-or-clear') closeLibraryPanel();
        return;
      }
      if (contextMenu) {
        if (action === 'close-or-clear') contextMenu = null;
        return;
      }
      if (selection.lightboxId !== null) {
        handleLightboxAction(action, e);
        return;
      }
      if (action === 'close-or-clear' && document.activeElement?.id === 'search-input') return;
      handleGridAction(action, e);
    }
    window.addEventListener('keydown', onKeydown);
    return () => window.removeEventListener('keydown', onKeydown);
  });
</script>

<div class="app-shell">
  <TopBar onHelp={() => (showHelp = !showHelp)} />
  <div class="body">
    <button class="drawer-toggle" aria-label="Toggle sidebar" onclick={toggleSidebar}>☰</button>
    <Sidebar />
    <div class="main">
      {#if isNoLibrary()}
        <div class="no-library">
          <h1>Choose your meme folder</h1>
          <p>Pick a folder to index and browse as your media library.</p>
          <LibraryChooser inline />
        </div>
      {:else}
        <FilterBar />
        {#if viewState.mode === 'duplicates'}
          <div class="view-header">
            <button class="btn small" onclick={showGallery}>← Back to library</button>
          </div>
          <DuplicatesView />
        {:else}
          <Gallery onContextMenu={openContextMenu} />
        {/if}
      {/if}
    </div>
  </div>
  <StatsBar />
</div>

{#if selection.lightboxId !== null}
  <Lightbox />
{/if}

{#if contextMenu}
  <ContextMenu item={contextMenu.item} x={contextMenu.x} y={contextMenu.y} onClose={() => (contextMenu = null)} />
{/if}

{#if libraryState.panelOpen}
  <LibraryPanel />
{/if}

<Toasts />
<DropZone enabled={config?.inbox_enabled ?? false} />

{#if showHelp}
  <ShortcutsHelp onclose={() => (showHelp = false)} />
{/if}

<style>
  .app-shell {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
  }
  .body {
    display: flex;
    flex: 1;
    min-height: 0;
  }
  .main {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-width: 0;
    min-height: 0;
  }
  .view-header {
    padding: 8px 16px;
  }
  .no-library {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 6px;
    padding: 40px 20px;
    overflow-y: auto;
    text-align: center;
  }
  .no-library h1 {
    margin: 0;
    font-size: 20px;
  }
  .no-library p {
    margin: 0 0 20px;
    color: var(--text-muted);
    font-size: 13px;
  }
  .btn.small {
    padding: 4px 10px;
    font-size: 12px;
  }
  .drawer-toggle {
    display: none;
  }
  @media (max-width: 860px) {
    .drawer-toggle {
      display: flex;
      align-items: center;
      justify-content: center;
      position: fixed;
      bottom: 16px;
      left: 16px;
      width: 44px;
      height: 44px;
      border-radius: 50%;
      border: 1px solid var(--border);
      background: var(--bg-elevated);
      box-shadow: var(--shadow);
      z-index: 380;
      font-size: 18px;
    }
  }
</style>
