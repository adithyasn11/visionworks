// frontend/app/lib/evidence.js
//
// Fetching an evidence picture: the blurred image kept for a flagged decision.
//
// The image is behind an authenticated endpoint (the backend checks the caller's
// organisation and role, and writes an audit row for every view), so it cannot be
// an <img src>: a plain image request carries no Authorization header. It is fetched
// with the access token and shown from a short-lived object URL instead, which the
// caller revokes when the picture is closed.

import { backendFetch } from './backend';

/** What to tell the person for each way the request can fail. */
export function evidenceMessage(status) {
  switch (status) {
    case 401: return 'Sign in again to see evidence pictures.';
    case 403: return 'Only an administrator or manager can see evidence pictures.';
    case 404: return 'No picture was stored for this decision.';
    case 410: return 'This picture has expired and was deleted.';
    case 503: return 'This view could not be recorded, so the picture was not shown. Try again.';
    default: return 'The picture could not be loaded.';
  }
}

/**
 * @param {string} decisionId
 * @returns {Promise<{ url: string } | { error: string }>}
 */
export async function loadEvidence(decisionId) {
  if (!decisionId) return { error: evidenceMessage(404) };
  try {
    const response = await backendFetch(`/api/v1/safety/decisions/${encodeURIComponent(decisionId)}/snapshot`);
    if (!response.ok) return { error: evidenceMessage(response.status) };
    const blob = await response.blob();
    return { url: URL.createObjectURL(blob) };
  } catch {
    return { error: evidenceMessage(0) };
  }
}
