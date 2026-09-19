// Shared item actions (favorite, copy, open, reveal) used by Card, ContextMenu, Lightbox and
// the global keyboard shortcuts, so behaviour + toast copy stays in one place.
import { api, ApiError } from './api';
import { pushToast, results, scheduleCountsRefresh } from './state.svelte';
import type { Item } from './types';

function patchItem(id: number, patch: Partial<Item>): void {
  const item = results.items.find((it) => it.id === id);
  if (item) Object.assign(item, patch);
}

export async function toggleFavorite(item: Item): Promise<void> {
  const next = !item.favorite;
  patchItem(item.id, { favorite: next }); // optimistic; card/lightbox heart updates instantly
  try {
    const res = await api.setFavorite(item.id, next);
    patchItem(item.id, { favorite: res.favorite });
    scheduleCountsRefresh(); // syncs the sidebar's Favorites count and the stats footer
  } catch {
    patchItem(item.id, { favorite: item.favorite }); // revert optimistic update
  }
}

export async function copyPath(item: Item): Promise<void> {
  try {
    await navigator.clipboard.writeText(item.path);
    pushToast('Copied path', 'success');
  } catch {
    pushToast('Could not copy path', 'error');
  }
}

/** Images: draw to canvas and copy PNG bytes. GIF/video: clipboard can't hold animated media,
 * so fall back to copying the path with an explanatory toast. */
export async function copyMedia(item: Item): Promise<void> {
  if (item.media_type !== 'image') {
    await copyPath(item);
    pushToast("Copied path (animated media can't be copied as an image)", 'info');
    return;
  }
  try {
    const img = await loadImage(item.media_url);
    const canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('no 2d context');
    ctx.drawImage(img, 0, 0);
    const blob: Blob | null = await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
    if (!blob) throw new Error('toBlob failed');
    const ClipboardItemCtor = (window as unknown as { ClipboardItem?: typeof ClipboardItem }).ClipboardItem;
    if (!ClipboardItemCtor) throw new Error('Clipboard image API unsupported');
    await navigator.clipboard.write([new ClipboardItemCtor({ 'image/png': blob })]);
    pushToast('Copied image', 'success');
  } catch {
    pushToast('Could not copy image, copying path instead', 'error');
    await copyPath(item);
  }
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('image failed to load'));
    img.src = src;
  });
}

export async function openOriginal(item: Item): Promise<void> {
  try {
    await api.openOriginal(item.id);
  } catch (err) {
    if (!(err instanceof ApiError)) pushToast('Could not open file', 'error');
  }
}

export async function revealInFolder(item: Item): Promise<void> {
  try {
    await api.reveal(item.id);
  } catch (err) {
    if (!(err instanceof ApiError)) pushToast('Could not reveal file', 'error');
  }
}

export function downloadUrl(item: Item): string {
  return item.media_url;
}
