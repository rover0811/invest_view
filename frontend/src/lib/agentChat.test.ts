import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  createAgentSession,
  streamAgentChat,
  regenerateAgentChat,
  type AgentStreamCallbacks,
} from './api';

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    json: async () => body,
  } as Response;
}

function streamFrom(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  let i = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i < chunks.length) controller.enqueue(enc.encode(chunks[i++]));
      else controller.close();
    },
  });
}

function streamResponse(chunks: string[], status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    body: streamFrom(chunks),
  } as unknown as Response;
}

function collectingCallbacks() {
  const tokens: string[] = [];
  let done: { message_id: string; status: string; sibling_count?: number } | null = null;
  let error: string | null = null;
  const cbs: AgentStreamCallbacks = {
    onToken: (t) => tokens.push(t),
    onDone: (d) => {
      done = d;
    },
    onError: (m) => {
      error = m;
    },
  };
  return {
    cbs,
    tokens,
    get done() {
      return done;
    },
    get error() {
      return error;
    },
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe('streamAgentChat SSE parsing', () => {
  it('parses token/done frames even when split across chunk boundaries', async () => {
    const chunks = [
      'event: token\ndata: {"te',
      'xt":"A"}\n\nevent: token\ndata: {"text":"B"}\n',
      '\nevent: done\ndata: {"message_id":"m1","status":"complete"}\n\n',
    ];
    vi.stubGlobal('fetch', vi.fn(async () => streamResponse(chunks)));

    const c = collectingCallbacks();
    await streamAgentChat('s1', 'hi', c.cbs);

    expect(c.tokens).toEqual(['A', 'B']);
    expect(c.done).not.toBeNull();
    expect(c.done!.message_id).toBe('m1');
    expect(c.done!.status).toBe('complete');
    expect(c.error).toBeNull();
  });

  it('handles a final frame not terminated by a trailing blank line', async () => {
    const chunks = ['event: token\ndata: {"text":"X"}\n\nevent: done\ndata: {"message_id":"m9","status":"complete"}'];
    vi.stubGlobal('fetch', vi.fn(async () => streamResponse(chunks)));

    const c = collectingCallbacks();
    await streamAgentChat('s1', 'hi', c.cbs);

    expect(c.tokens).toEqual(['X']);
    expect(c.done!.message_id).toBe('m9');
  });

  it('routes error events to onError', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => streamResponse(['event: error\ndata: {"message":"boom"}\n\n'])));

    const c = collectingCallbacks();
    await streamAgentChat('s1', 'hi', c.cbs);

    expect(c.error).toBe('boom');
    expect(c.tokens).toEqual([]);
  });

  it('reports a 401 response as an auth error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({ ok: false, status: 401, statusText: 'Unauthorized' }) as Response),
    );

    const c = collectingCallbacks();
    await streamAgentChat('s1', 'hi', c.cbs);

    expect(c.error).toBe('인증이 필요합니다');
  });

  it('treats an aborted fetch as a silent stop (no onError)', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        const e = new Error('aborted');
        e.name = 'AbortError';
        throw e;
      }),
    );

    const c = collectingCallbacks();
    await streamAgentChat('s1', 'hi', c.cbs);

    expect(c.error).toBeNull();
    expect(c.tokens).toEqual([]);
  });

  it('sends text + parent_id in the stream request body', async () => {
    const f = vi.fn(async () => streamResponse(['event: done\ndata: {"message_id":"m","status":"complete"}\n\n']));
    vi.stubGlobal('fetch', f);

    const c = collectingCallbacks();
    await streamAgentChat('sess42', 'why up?', c.cbs, { parentId: 'p1' });

    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/agent/sessions/sess42/stream');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ text: 'why up?', parent_id: 'p1' });
  });
});

describe('regenerateAgentChat', () => {
  it('posts to the regenerate URL and streams the new sibling', async () => {
    const f = vi.fn(async () =>
      streamResponse([
        'event: token\ndata: {"text":"Z"}\n\nevent: done\ndata: {"message_id":"m2","status":"complete","sibling_count":2}\n\n',
      ]),
    );
    vi.stubGlobal('fetch', f);

    const c = collectingCallbacks();
    await regenerateAgentChat('sess1', 'msgA', c.cbs);

    const [url] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/agent/sessions/sess1/messages/msgA/regenerate');
    expect(c.tokens).toEqual(['Z']);
    expect(c.done!.sibling_count).toBe(2);
  });
});

describe('createAgentSession', () => {
  it('posts the ticker to /api/agent/sessions', async () => {
    const f = vi.fn(async () => jsonResponse({ session_id: 'sess1', ticker: '005930' }, 201));
    vi.stubGlobal('fetch', f);

    const s = await createAgentSession('005930');

    expect(s.session_id).toBe('sess1');
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/agent/sessions');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body as string)).toEqual({ ticker: '005930' });
  });

  it('attaches the Authorization header when a dev JWT is configured', async () => {
    vi.stubEnv('VITE_DEV_JWT', 'devtoken');
    const f = vi.fn(async () => jsonResponse({ session_id: 'sess1', ticker: '005930' }, 201));
    vi.stubGlobal('fetch', f);

    await createAgentSession('005930');

    const init = (f.mock.calls[0] as unknown as [string, RequestInit])[1];
    const headers = init.headers as Record<string, string>;
    expect(headers['Authorization']).toBe('Bearer devtoken');
  });

  it('omits the Authorization header when no token is available', async () => {
    vi.stubEnv('VITE_DEV_JWT', '');
    if (typeof window !== 'undefined') window.localStorage.removeItem('authToken');
    const f = vi.fn(async () => jsonResponse({ session_id: 'sess1', ticker: '005930' }, 201));
    vi.stubGlobal('fetch', f);

    await createAgentSession('005930');

    const init = (f.mock.calls[0] as unknown as [string, RequestInit])[1];
    const headers = init.headers as Record<string, string>;
    expect(headers['Authorization']).toBeUndefined();
  });

  it('throws an auth error on 401', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'no' }, 401)));
    await expect(createAgentSession('005930')).rejects.toThrow('인증이 필요합니다');
  });
});
