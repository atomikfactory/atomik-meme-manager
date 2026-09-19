<script lang="ts">
  import { SHORTCUT_HELP } from '../lib/shortcuts';

  interface Props {
    onclose: () => void;
  }
  let { onclose }: Props = $props();
</script>

<div class="scrim" role="presentation" onclick={onclose}>
  <div
    class="panel"
    role="dialog"
    aria-modal="true"
    tabindex="-1"
    aria-label="Keyboard shortcuts"
    onclick={(e) => e.stopPropagation()}
  >
    <header>
      <h2>Keyboard shortcuts</h2>
      <button class="icon-btn" aria-label="Close" onclick={onclose}>✕</button>
    </header>
    <ul>
      {#each SHORTCUT_HELP as s (s.keys)}
        <li>
          <kbd>{s.keys}</kbd>
          <span>{s.description}</span>
        </li>
      {/each}
    </ul>
  </div>
</div>

<style>
  .scrim {
    position: fixed;
    inset: 0;
    background: var(--overlay);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 600;
    animation: fade var(--transition-med);
  }
  .panel {
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow);
    width: min(480px, 92vw);
    max-height: 80vh;
    overflow: auto;
    padding: 8px 0 16px;
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 18px;
    border-bottom: 1px solid var(--border);
  }
  h2 {
    margin: 0;
    font-size: 15px;
  }
  ul {
    list-style: none;
    margin: 8px 0 0;
    padding: 0 18px;
  }
  li {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 7px 0;
    border-bottom: 1px dashed var(--border);
    font-size: 13px;
  }
  li:last-child {
    border-bottom: none;
  }
  kbd {
    font-family: var(--font-mono);
    background: var(--bg-sunken);
    border: 1px solid var(--border);
    border-radius: 5px;
    padding: 2px 7px;
    font-size: 11.5px;
    white-space: nowrap;
  }
  span {
    color: var(--text-muted);
    text-align: right;
  }
  @keyframes fade {
    from {
      opacity: 0;
    }
    to {
      opacity: 1;
    }
  }
</style>
