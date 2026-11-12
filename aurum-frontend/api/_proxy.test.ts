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
  delete process.env.AURUM_MT5_BACKEND_URL;
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

  it('sends the MT5 link to the MT5 PC and everything else to the main backend', async () => {
    process.env.AURUM_BACKEND_URL = 'https://api.example.com';
    process.env.AURUM_MT5_BACKEND_URL = 'https://mt5-pc.example.com/';
    stubBackend(new Response('{}'));
    await handler(new Request('https://app.vercel.app/api/proxy?__path=v1/mt5/connection'));
    await handler(new Request('https://app.vercel.app/api/proxy?__path=v1/mt5movers'));
    await handler(new Request('https://app.vercel.app/api/proxy?__path=v1/auth/me'));
    expect(calls.map((c) => c.url)).toEqual([
      'https://mt5-pc.example.com/api/v1/mt5/connection',
      'https://api.example.com/api/v1/mt5movers',
      'https://api.example.com/api/v1/auth/me',
    ]);
  });

  it('falls back to the main backend for the MT5 link when no MT5 PC is set', async () => {
    process.env.AURUM_BACKEND_URL = 'https://api.example.com';
    stubBackend(new Response('{}'));
    await handler(new Request('https://app.vercel.app/api/proxy?__path=v1/mt5/status'));
    expect(calls[0].url).toBe('https://api.example.com/api/v1/mt5/status');
  });

  it('retries the MT5 link when the PC does not answer, then succeeds', async () => {
    process.env.AURUM_BACKEND_URL = 'https://api.example.com';
    process.env.AURUM_MT5_BACKEND_URL = 'https://mt5-pc.example.com';
    let n = 0;
    vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      n += 1;
      if (n === 1) throw new TypeError('fetch failed');
      if (n === 2) return new Response('<html>tunnel down</html>', { status: 502, headers: { 'content-type': 'text/html' } });
      return new Response('{"connection":null}', { status: 200, headers: { 'content-type': 'application/json' } });
    });
    const r = await handler(new Request('https://app.vercel.app/api/proxy?__path=v1/mt5/connect', { method: 'POST', body: '{}' }));
    expect(r.status).toBe(200);
    expect(calls).toHaveLength(3);
  });

  it('never repeats other writes (an approval must not be sent twice)', async () => {
    process.env.AURUM_BACKEND_URL = 'https://api.example.com';
    vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      throw new TypeError('fetch failed');
    });
    const r = await handler(new Request('https://app.vercel.app/api/proxy?__path=v1/approvals/1/approve', { method: 'POST', body: '{}' }));
    expect(r.status).toBe(502);
    expect(calls).toHaveLength(1);
  });

  it('explains clearly when the trading PC stays unreachable', async () => {
    process.env.AURUM_BACKEND_URL = 'https://api.example.com';
    process.env.AURUM_MT5_BACKEND_URL = 'https://mt5-pc.example.com';
    vi.stubGlobal('fetch', async () => {
      throw new TypeError('fetch failed');
    });
    const r = await handler(new Request('https://app.vercel.app/api/proxy?__path=v1/mt5/connection'));
    expect(r.status).toBe(502);
    expect((await r.json()).error.message).toMatch(/trading PC/);
  });
});
