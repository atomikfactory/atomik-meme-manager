<script lang="ts">
  import { api } from '../lib/api';
  import { pushToast } from '../lib/state.svelte';

  interface Props {
    enabled: boolean;
  }
  let { enabled }: Props = $props();

  let dragging = $state(false);
  let dragDepth = 0;

  function onDragEnter(e: DragEvent): void {
    if (!enabled || !e.dataTransfer?.types.includes('Files')) return;
    dragDepth++;
    dragging = true;
  }

  function onDragLeave(): void {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) dragging = false;
  }

  function onDragOver(e: DragEvent): void {
    if (!enabled) return;
    e.preventDefault();
  }

  async function onDrop(e: DragEvent): Promise<void> {
    e.preventDefault();
    dragDepth = 0;
    dragging = false;
    if (!enabled) return;
    const files = Array.from(e.dataTransfer?.files ?? []);
    if (files.length === 0) return;
    try {
      const res = await api.uploadInbox(files);
      pushToast(`Saved ${res.saved.length} file${res.saved.length === 1 ? '' : 's'} to inbox`, 'success');
    } catch {
      // error toast already surfaced by apiFetch
    }
  }

  $effect(() => {
    window.addEventListener('dragenter', onDragEnter);
    window.addEventListener('dragleave', onDragLeave);
    window.addEventListener('dragover', onDragOver);
    window.addEventListener('drop', onDrop);
    return () => {
      window.removeEventListener('dragenter', onDragEnter);
      window.removeEventListener('dragleave', onDragLeave);
      window.removeEventListener('dragover', onDragOver);
      window.removeEventListener('drop', onDrop);
    };
  });
</script>

{#if dragging}
  <div class="dropzone" role="presentation">
    <div class="card">
      <div class="icon">⬇</div>
      <p>Drop to add to inbox</p>
    </div>
  </div>
{/if}

<style>
  .dropzone {
    position: fixed;
    inset: 0;
    z-index: 550;
    background: color-mix(in srgb, var(--accent) 14%, var(--overlay));
    border: 3px dashed var(--accent);
    display: flex;
    align-items: center;
    justify-content: center;
    pointer-events: none;
    animation: fade var(--transition-fast);
  }
  .card {
    background: var(--bg-elevated);
    border-radius: var(--radius-lg);
    padding: 28px 36px;
    text-align: center;
    box-shadow: var(--shadow);
  }
  .icon {
    font-size: 32px;
  }
  p {
    margin: 8px 0 0;
    font-weight: 600;
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
