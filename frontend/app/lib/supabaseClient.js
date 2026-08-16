// frontend/app/lib/supabaseClient.js
import { createClient } from '@supabase/supabase-js';

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

/**
 * True when both env vars are present and are not the placeholder values from
 * .env.local.example. The auth screens check this so they can show a clear
 * "not configured" message instead of failing with an opaque network error.
 */
export const isSupabaseConfigured = Boolean(
  supabaseUrl &&
  supabaseAnonKey &&
  !supabaseUrl.includes('your-supabase') &&
  !supabaseAnonKey.includes('your-supabase')
);

// Null rather than a client built from placeholders — a half-configured client
// throws deep inside the SDK, which is much harder to diagnose.
export const supabase = isSupabaseConfigured
  ? createClient(supabaseUrl, supabaseAnonKey, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        detectSessionInUrl: true,
      },
    })
  : null;
