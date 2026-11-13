'use server';

// frontend/app/signup/actions.js
//
// Email + password sign-up without the confirmation email. The account is created on the server through the
// Supabase admin API with the email already marked confirmed, so the page can sign the person in straight
// away. This does not depend on the project's "Confirm email" setting.
//
// The service-role key is read here only (SUPABASE_SERVICE_ROLE_KEY, no NEXT_PUBLIC_ prefix), so it never
// reaches the browser. Without it the page falls back to the ordinary sign-up.

const URL_ = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SERVICE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY;

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export async function createConfirmedAccount({ name, email, password }) {
  if (!URL_ || !SERVICE_KEY) return { fallback: true };

  const cleanEmail = String(email ?? '').trim().toLowerCase();
  const cleanName = String(name ?? '').trim();
  const pw = String(password ?? '');
  // Same rules the form shows; checked again here because the browser can be bypassed.
  if (!cleanName) return { error: 'Enter your name.' };
  if (!EMAIL_RE.test(cleanEmail)) return { error: 'That email address doesn’t look right.' };
  if (pw.length < 8 || !/[a-z]/.test(pw) || !/[A-Z]/.test(pw) || !/\d/.test(pw)) {
    return { error: 'Your password doesn’t meet the requirements yet.' };
  }

  let res;
  try {
    res = await fetch(`${URL_}/auth/v1/admin/users`, {
      method: 'POST',
      headers: { apikey: SERVICE_KEY, Authorization: `Bearer ${SERVICE_KEY}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: cleanEmail,
        password: pw,
        email_confirm: true,
        user_metadata: { full_name: cleanName },
      }),
      cache: 'no-store',
    });
  } catch {
    return { error: 'Couldn’t reach the authentication service. Check that your Supabase project is active.' };
  }

  if (res.ok) return { ok: true };

  const body = await res.json().catch(() => ({}));
  const msg = String(body?.msg || body?.message || body?.error_description || body?.error || '');
  if (res.status === 422 || /already (been )?registered|already exists/i.test(msg)) {
    return { error: 'An account with that email already exists. Try signing in instead.' };
  }
  return { error: msg || 'Could not create the account. Please try again.' };
}
