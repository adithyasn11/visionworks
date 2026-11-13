// frontend/app/lib/backend.js
//
// Talking to the FastAPI CV backend as an authenticated tenant.
//
// WHY EVERY CALL CARRIES A TOKEN
//
// The backend is a separate origin with permissive CORS and no session of its
// own. It therefore cannot know who is calling from cookies — the Supabase
// session cookie is scoped to the Next.js origin and is never sent to
// localhost:8001. So the browser passes its Supabase **access token**
// explicitly, and the backend verifies the signature and derives the
// organisation from it (see backend/app/api/deps.py).
//
// The client never tells the backend which organisation it is. That value is
// derived server-side from the verified token, because anything the client
// asserts about its own tenancy is editable in a URL bar. A request with no
// token, an expired one, or a forged one resolves to no tenant and reads
// nothing — the backend fails closed.

import { supabase } from './supabase/browser';

/**
 * Where the backend lives, derived from the page rather than hardcoded.
 *
 * IT USED TO BE `http://localhost:8001`, AND THAT BROKE TWICE OVER.
 *
 * When the launcher runs with --https the backend serves TLS, so a plain
 * http:// request to that port gets no response at all — and a page loaded
 * over https is FORBIDDEN from calling http:// anyway, because browsers block
 * mixed content silently. The dashboard rendered fine and every backend call
 * failed, which reads as "the backend is down" rather than "the scheme is
 * wrong".
 *
 * It also broke the phone. A phone loading the dashboard at
 * https://192.168.3.5:3000 would try to reach the backend at
 * `localhost:8001`, and on a phone localhost is the phone.
 *
 * So both parts follow the page: same protocol, same hostname, backend port.
 * An explicit NEXT_PUBLIC_BACKEND_HTTP still wins, for a deployment where the
 * backend genuinely lives somewhere else.
 */
function inferBackendOrigin() {
  if (process.env.NEXT_PUBLIC_BACKEND_HTTP) {
    return process.env.NEXT_PUBLIC_BACKEND_HTTP;
  }
  // Server-side render: no window, and nothing in this module is called
  // during SSR anyway. The literal keeps the export a string.
  if (typeof window === 'undefined') return 'http://localhost:8001';
  return `${window.location.protocol}//${window.location.hostname}:8001`;
}

export const BACKEND_HTTP = inferBackendOrigin();

export const BACKEND_WS = BACKEND_HTTP.replace(/^http/, 'ws');

/**
 * The current access token, or null when signed out.
 *
 * getSession() rather than getUser() here, deliberately — and this is the one
 * place that is correct. getUser() validates against the auth server but
 * returns a user, not a token; the token is what the backend needs in order to
 * do its own validation. The token is not being trusted on this side: it is
 * being forwarded to a service that verifies it. supabase-js refreshes it
 * transparently, so this reads a live token rather than a stale one.
 */
export async function getAccessToken() {
  if (!supabase) return null;
  try {
    // Bounded: an expired session is refreshed here first, and if that refresh stalls every screen
    // used to wait on it forever (skeletons that never resolved). After a few seconds the call goes
    // out without a token and the screen shows "sign in again" instead of loading endlessly.
    const { data } = await withTimeout(supabase.auth.getSession(), 8000);
    return data?.session?.access_token ?? null;
  } catch {
    return null;
  }
}

/** Resolve with `promise`, or reject after `ms` so a stalled call can never hang a screen. */
export function withTimeout(promise, ms, message = 'This is taking too long. Refresh the page to try again.') {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(message)), ms); }),
  ]).finally(() => clearTimeout(timer));
}

/**
 * fetch() against the backend with the bearer token attached.
 *
 * Signed out, the call still goes out — and comes back empty rather than
 * failing, because the backend treats "no tenant" as "no rows". The dashboard
 * then renders its ordinary empty state instead of an error, which is the
 * right behaviour for a session that expired in a background tab.
 */
export async function backendFetch(path, options = {}) {
  const token = await getAccessToken();
  const headers = new Headers(options.headers || {});
  if (token) headers.set('Authorization', `Bearer ${token}`);

  // Reads get a time limit so an unreachable backend shows an error instead of a skeleton forever.
  // Uploads and other writes do not: a large video can legitimately take minutes.
  const method = (options.method || 'GET').toUpperCase();
  const signal = options.signal
    ?? (method === 'GET' && typeof AbortSignal !== 'undefined' && AbortSignal.timeout ? AbortSignal.timeout(30000) : undefined);

  return fetch(`${BACKEND_HTTP}${path}`, {
    cache: 'no-store',
    ...options,
    headers,
    ...(signal ? { signal } : {}),
  });
}

/**
 * A backend WebSocket URL with the token in the query string.
 *
 * Browsers cannot set headers on a WebSocket handshake — there is no API for
 * it — so the token travels as a query parameter. That is the standard
 * workaround and is safe in deployment, where the handshake is TLS-encrypted
 * and the token is short-lived. Returns the bare URL when signed out; the
 * backend will process the video but attribute the telemetry to nobody.
 */
export async function backendSocketUrl(path) {
  const token = await getAccessToken();
  const url = `${BACKEND_WS}${path}`;
  return token ? `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}` : url;
}
