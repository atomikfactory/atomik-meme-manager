<script lang="ts">
  import { toastState, dismissToast } from '../lib/state.svelte';
</script>

<div class="toasts" role="status" aria-live="polite">
  {#each toastState.items as toast (toast.id)}
    <div class="toast {toast.kind}">
      <span>{toast.message}</span>
      <button class="dismiss" aria-label="Dismiss" onclick={() => dismissToast(toast.id)}>×</button>
    </div>
  {/each}
</div>

<style>
  .toasts {
    position: fixed;
    right: 16px;
    bottom: 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    z-index: 500;
    max-width: min(360px, calc(100vw - 32px));
  }
  .toast {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    border-radius: var(--radius-md);
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    box-shadow: var(--shadow);
    font-size: 13px;
    color: var(--text);
    animation: rise var(--transition-med);
  }
  .toast.error {
    border-color: color-mix(in srgb, var(--danger) 55%, var(--border));
  }
  .toast.success {
    border-color: color-mix(in srgb, var(--accent-2) 55%, var(--border));
  }
  .toast span {
    flex: 1;
  }
  .dismiss {
    background: none;
    border: none;
    color: var(--text-faint);
    font-size: 16px;
    line-height: 1;
    padding: 0 2px;
  }
  .dismiss:hover {
    color: var(--text);
  }
  @keyframes rise {
    from {
      opacity: 0;
      transform: translateY(6px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }
</style>
