// frontend/app/lib/supabase/browser.js
//
// The browser-side Supabase client.
//
// WHY THIS REPLACES createClient() FROM supabase-js
//
// The plain `createClient` stores the session in localStorage. That works for a
// purely client-rendered app, but it makes server-side route protection
// impossible: middleware and Server Components receive only cookies, and
// localStorage is invisible to them. Any "guard" would have to run in the
// browser AFTER the page had already been sent — which is a UX nicety, not a
// security boundary.
//
// `createBrowserClient` from @supabase/ssr writes the session to cookies
// instead, so the same session is readable by middleware, Server Components,
// Route Handlers, and the browser. That is what lets /platform be gated before
// a single byte of it reaches a non-operator.
//
// Use this in any 'use client' component.

import { createBrowserClient } from '@supabase/ssr';

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

/**
 * True when both env vars are present and are not the placeholders from
 * .env.local.example. The auth screens check this so they can show a clear
 * "not configured" message instead of failing with an opaque network error.
 */
export const isSupabaseConfigured = Boolean(
  url && anonKey && !url.includes('your-supabase') && !anonKey.includes('your-supabase'),
);

/**
 * One client per browser context. `createBrowserClient` is already a singleton
 * internally, but returning null when unconfigured keeps the failure legible —
 * a client built from placeholder values throws deep inside the SDK.
 */
export function createClient() {
  if (!isSupabaseConfigured) return null;
  return createBrowserClient(url, anonKey);
}

/** Convenience singleton for components that just want to call auth methods. */
export const supabase = isSupabaseConfigured ? createBrowserClient(url, anonKey) : null;
