// Underscore prefix: Vercel does not deploy this file as a function.
import { afterEach, describe, expect, it, vi } from 'vitest';
import handler from './proxy';

const calls: { url: string; init: RequestInit }[] = [];

function stubBackend(res: Response) {
  vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return res;
  });
}

afterEach(() => {
  calls.length = 0;
  vi.unstubAllGlobals();
  delete process.env.AURUM_BACKEND_URL;
});

describe('Vercel API proxy', () => {
  it('says clearly when the backend address is not configured', async () => {
    const r = await handler(new Request('https://app.vercel.app/api/proxy?__path=v1/health'));
    expect(r.status).toBe(503);
    expect((await r.json()).error.code).toBe('BACKEND_NOT_CONFIGURED');
  });

  it('forwards method, path, query, body, cookie and CSRF header, and returns the session cookie', async () => {
    process.env.AURUM_BACKEND_URL = 'https://api.example.com/';
    const up = new Response('{"ok":true}', { status: 200, headers: { 'content-type': 'application/json' } });
    up.headers.append('set-cookie', 'aurum_session=abc; HttpOnly; Path=/; SameSite=lax');
    stubBackend(up);
    const r = await handler(new Request('https://app.vercel.app/api/proxy?__path=v1/auth/login&x=1', {
      method: 'POST', body: '{"username":"a"}', headers: { cookie: 'c=1', 'x-csrf-token': 't', 'content-type': 'application/json' },
    }));
    expect(calls[0].url).toBe('https://api.example.com/api/v1/auth/login?x=1');
    expect(calls[0].init.method).toBe('POST');
    const h = calls[0].init.headers as Headers;
    expect(h.get('cookie')).toBe('c=1');
    expect(h.get('x-csrf-token')).toBe('t');
    expect(h.get('x-forwarded-host')).toBe('app.vercel.app');
    expect(new TextDecoder().decode(calls[0].init.body as ArrayBuffer)).toBe('{"username":"a"}');
    expect(r.status).toBe(200);
    expect(r.headers.get('set-cookie')).toContain('aurum_session=abc');
  });

  it('only forwards the v1 API and refuses path tricks', async () => {
    process.env.AURUM_BACKEND_URL = 'https://api.example.com';
    stubBackend(new Response('x'));
    for (const p of ['admin', 'v1/../secret', 'v2/x', 'v1/a b']) {
      const r = await handler(new Request(`https://app.vercel.app/api/proxy?__path=${encodeURIComponent(p)}`));
      expect(r.status).toBe(404);
    }
    expect(calls).toHaveLength(0);
  });

  it('reports an unreachable backend', async () => {
    process.env.AURUM_BACKEND_URL = 'https://api.example.com';
    vi.stubGlobal('fetch', async () => {
      throw new TypeError('fetch failed');
    });
    const r = await handler(new Request('https://app.vercel.app/api/proxy?__path=v1/health'));
    expect(r.status).toBe(502);
  });
});
