/**
 * Vercel Edge Function: forwards /api/v1/* from the deployed frontend to the AURUM backend.
 *
 * Why a proxy: sign-in uses an httpOnly SameSite=Lax session cookie. Calling the backend on another domain would make the
 * cookie cross-site and the browser would drop it. Through this proxy the browser only ever talks to the Vercel domain.
 *
 * Configure on Vercel (Project → Settings → Environment Variables):
 *   AURUM_BACKEND_URL     = https://your-backend.example.com   (server-side only; NOT a VITE_ variable)
 *   AURUM_MT5_BACKEND_URL = https://the-mt5-pc.example.com     (optional) where /api/v1/mt5/* goes: the per-person MT5
 *                           link must run on the Windows PC that holds the MT5 terminal slots. Both backends must use
 *                           the same database, so a sign-in on one is valid on the other.
 * vercel.json rewrites /api/v1/:path* to /api/proxy?__path=v1/:path*.
 */
export const config = { runtime: 'edge' };

const HOP_BY_HOP = new Set([
  'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade',
  'host', 'content-length', 'content-encoding',
]);

function json(status: number, code: string, message: string): Response {
  return new Response(JSON.stringify({ error: { code, message }, detail: message }), {
    status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
  });
}

const clean = (v: string | undefined) => (v ?? '').trim().replace(/\/+$/, '');

export default async function handler(request: Request): Promise<Response> {
  const incoming = new URL(request.url);
  const path = incoming.searchParams.get('__path') ?? '';
  if (!/^v1(\/[A-Za-z0-9._~\-/%]*)?$/.test(path) || path.split('/').some((s) => s === '..' || s === '.'))
    return json(404, 'NOT_FOUND', 'Not found.');

  const mt5 = path === 'v1/mt5' || path.startsWith('v1/mt5/');
  const backend = (mt5 && clean(process.env.AURUM_MT5_BACKEND_URL)) || clean(process.env.AURUM_BACKEND_URL);
  if (!/^https?:\/\//.test(backend))
    return json(503, 'BACKEND_NOT_CONFIGURED', 'The AURUM backend address is not configured for this deployment (AURUM_BACKEND_URL).');

  incoming.searchParams.delete('__path');
  const target = `${backend}/api/${path}${incoming.search}`;

  const headers = new Headers();
  request.headers.forEach((v, k) => {
    if (!HOP_BY_HOP.has(k.toLowerCase())) headers.set(k, v);
  });
  headers.set('x-forwarded-host', incoming.host);
  headers.set('x-forwarded-proto', incoming.protocol.replace(':', ''));

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === 'GET' || request.method === 'HEAD' ? undefined : await request.arrayBuffer(),
      redirect: 'manual',
    });
  } catch {
    return json(502, 'BACKEND_UNREACHABLE', 'The AURUM backend could not be reached.');
  }

  const out = new Headers();
  upstream.headers.forEach((v, k) => {
    if (k.toLowerCase() !== 'set-cookie' && !HOP_BY_HOP.has(k.toLowerCase())) out.set(k, v);
  });
  const cookies = typeof upstream.headers.getSetCookie === 'function' ? upstream.headers.getSetCookie() : [];
  for (const c of cookies) out.append('set-cookie', c);
  return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: out });
}
