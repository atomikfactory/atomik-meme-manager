import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, setApiErrorHandler } from './api';

function mockJsonFetch(body: unknown, opts: { ok?: boolean; status?: number } = {}): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: opts.ok ?? true,
    status: opts.status ?? 200,
    json: async () => body,
  } as Response);
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function headerFromCall(fetchMock: ReturnType<typeof vi.fn>, name: string): string | null {
  const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return new Headers(init.headers).get(name);
}

describe('apiFetch: X-Atomik-Meme CSRF header', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    setApiErrorHandler(() => {});
  });

  it('is sent on a POST request', async () => {
    const fetchMock = mockJsonFetch({ view_count: 1, last_viewed_at: '2026-01-01T00:00:00Z' });
    await api.recordView(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(headerFromCall(fetchMock, 'X-Atomik-Meme')).toBe('1');
  });

  it('is sent on a plain GET request too', async () => {
    const fetchMock = mockJsonFetch({ status: 'ok', version: '1', library_root: '', data_dir: '', ffmpeg: true, items: 0 });
    await api.health();
    expect(headerFromCall(fetchMock, 'X-Atomik-Meme')).toBe('1');
  });

  it('is sent on the multipart inbox upload, without breaking the browser-generated multipart Content-Type', async () => {
    const fetchMock = mockJsonFetch({ saved: ['a.txt'], inbox_dir: 'D:\\inbox' });
    const file = new File(['hello'], 'a.txt', { type: 'text/plain' });
    await api.uploadInbox([file]);

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(new Headers(init.headers).get('X-Atomik-Meme')).toBe('1');
    // We must not set our own Content-Type: FormData needs the browser to add the boundary.
    expect(new Headers(init.headers).has('Content-Type')).toBe(false);
    expect(init.body).toBeInstanceOf(FormData);
  });

  it('shows a toast that calls out a 403 distinctly, using the server-provided detail', async () => {
    mockJsonFetch({ detail: 'missing X-Atomik-Meme header' }, { ok: false, status: 403 });
    const toast = vi.fn();
    setApiErrorHandler(toast);

    await expect(api.health()).rejects.toMatchObject({ status: 403, detail: 'missing X-Atomik-Meme header' });
    expect(toast).toHaveBeenCalledTimes(1);
    const [message] = toast.mock.calls[0] as [string];
    expect(message).toContain('403');
    expect(message).toContain('missing X-Atomik-Meme header');
  });
});
