// frontend/app/lib/supabaseClient.js
//
// DEPRECATED SHIM — kept so existing imports keep working.
//
// This module used to build a client with `createClient` from supabase-js, which
// stores the session in localStorage. That made server-side route protection
// impossible: middleware and Server Components only receive cookies, so any
// guard had to run in the browser after the page was already sent.
//
// The session now lives in httpOnly cookies via @supabase/ssr. Everything is
// re-exported from lib/supabase/browser so no call site had to change and the
// two clients can never both be live at once (which would fight over the
// session and log users out unpredictably).
//
// New code should import from './supabase/browser' directly.

export { supabase, isSupabaseConfigured, createClient } from './supabase/browser';
