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

export const BACKEND_HTTP =
  process.env.NEXT_PUBLIC_BACKEND_HTTP || 'http://localhost:8001';

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
    const { data } = await supabase.auth.getSession();
    return data?.session?.access_token ?? null;
  } catch {
    return null;
  }
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

  return fetch(`${BACKEND_HTTP}${path}`, {
    cache: 'no-store',
    ...options,
    headers,
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
