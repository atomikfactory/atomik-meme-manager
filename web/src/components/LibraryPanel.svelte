<script lang="ts">
  import LibraryChooser from './LibraryChooser.svelte';
  import {
    closeLibraryPanel,
    confirmSwitchWarning,
    dismissSwitchWarning,
    libraryBasename,
    libraryState,
    revealLibrary,
  } from '../lib/state.svelte';

  let dialogEl: HTMLDivElement | undefined = $state();
  let previouslyFocused: HTMLElement | null = null;

  function focusableElements(): HTMLElement[] {
    if (!dialogEl) return [];
    return Array.from(
      dialogEl.querySelectorAll<HTMLElement>(
        'button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'
      )
    ).filter((el) => el.offsetParent !== null);
  }

  function onKeydown(e: KeyboardEvent): void {
    if (e.key !== 'Tab') return;
    const focusable = focusableElements();
    if (focusable.length === 0) return;
    const first = focusable[0]!;
    const last = focusable[focusable.length - 1]!;
    if (e.shiftKey) {
      if (document.activeElement === first || !dialogEl?.contains(document.activeElement)) {
        e.preventDefault();
        last.focus();
      }
    } else {
      if (document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }

  // Focus trap: remember what had focus, move focus into the dialog, restore on close.
  $effect(() => {
    previouslyFocused = document.activeElement as HTMLElement | null;
    const first = focusableElements()[0];
    first?.focus();
    return () => {
      previouslyFocused?.focus?.();
    };
  });
</script>

<div class="scrim" role="presentation">
  <div
    class="dialog"
    role="dialog"
    aria-modal="true"
    aria-label="Library"
    tabindex="-1"
    bind:this={dialogEl}
    onkeydown={onKeydown}
  >
    <header>
      <div class="current">
        <span class="glyph" aria-hidden="true">📁</span>
        <div class="path-block">
          <span class="label">Current library</span>
          <span class="path" title={libraryState.info?.library_root ?? undefined}>
            {libraryState.info?.library_root ?? 'No library selected'}
          </span>
        </div>
      </div>
      <div class="header-actions">
        <button class="btn small" disabled={!libraryState.info?.library_root} onclick={() => void revealLibrary()}>
          Reveal
        </button>
        <button
          class="icon-btn"
          aria-label="Close"
          disabled={libraryState.phase === 'switching'}
          onclick={closeLibraryPanel}
        >
          ✕
        </button>
      </div>
    </header>

    <div class="body">
      {#if libraryState.phase === 'switching'}
        {@const p = libraryState.progress}
        <div class="progress-view">
          <div class="spinner" aria-hidden="true"></div>
          <p class="progress-title">
            Indexing {libraryState.switchingTo ? libraryBasename(libraryState.switchingTo) : ''}…
          </p>
          {#if p && p.total > 0}
            <div class="progress-bar">
              <div class="progress-fill" style:width="{Math.min(100, (p.scanned / p.total) * 100)}%"></div>
            </div>
            <p class="progress-count">{p.scanned.toLocaleString()} / {p.total.toLocaleString()}</p>
          {:else}
            <p class="progress-count">Starting…</p>
          {/if}
        </div>
      {:else if libraryState.warning}
        <div class="notice">
          <p><strong>Heads up:</strong> {libraryState.warning}.</p>
          <p class="hint">Indexing has already started and may take a while.</p>
          <div class="notice-actions">
            <button class="btn" onclick={dismissSwitchWarning}>Cancel</button>
            <button class="btn primary" onclick={() => void confirmSwitchWarning()}>Continue</button>
          </div>
        </div>
      {:else}
        <LibraryChooser />
      {/if}
    </div>
  </div>
</div>

<style>
  .scrim {
    position: fixed;
    inset: 0;
    z-index: 750;
    background: var(--lightbox-backdrop);
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 16px;
    animation: fade var(--transition-med);
  }
  .dialog {
    width: min(520px, 100%);
    max-height: 86vh;
    display: flex;
    flex-direction: column;
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow);
    overflow: hidden;
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 14px 16px;
    border-bottom: 1px solid var(--border);
  }
  .current {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
  }
  .glyph {
    font-size: 20px;
  }
  .path-block {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .label {
    font-size: 10.5px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-faint);
  }
  .path {
    font-family: var(--font-mono);
    font-size: 12.5px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .header-actions {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }
  .btn.small {
    padding: 4px 10px;
    font-size: 12px;
  }
  .body {
    padding: 16px;
    overflow-y: auto;
  }
  .progress-view {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    padding: 30px 10px;
    text-align: center;
  }
  .spinner {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    border: 3px solid var(--border);
    border-top-color: var(--accent);
    animation: spin 0.8s linear infinite;
  }
  .progress-title {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
  }
  .progress-bar {
    width: 100%;
    max-width: 320px;
    height: 8px;
    border-radius: 999px;
    background: var(--bg-sunken);
    overflow: hidden;
  }
  .progress-fill {
    height: 100%;
    background: var(--accent);
    transition: width 300ms ease;
  }
  .progress-count {
    margin: 0;
    font-size: 12px;
    color: var(--text-faint);
    font-variant-numeric: tabular-nums;
  }
  .notice {
    background: color-mix(in srgb, var(--warning) 14%, transparent);
    border: 1px solid color-mix(in srgb, var(--warning) 40%, transparent);
    border-radius: var(--radius-md);
    padding: 14px;
  }
  .notice p {
    margin: 0 0 8px;
    font-size: 13px;
  }
  .notice .hint {
    color: var(--text-faint);
    font-size: 12px;
  }
  .notice-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    margin-top: 10px;
  }
  @keyframes fade {
    from {
      opacity: 0;
    }
    to {
      opacity: 1;
    }
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
</style>
