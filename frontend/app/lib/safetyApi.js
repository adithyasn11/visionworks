// frontend/app/lib/safetyApi.js
//
// The safety screens' client for the backend's safety API (backend/app/api/routers/safety.py).
//
// Every call carries the signed-in user's access token (see backend.js) and the backend runs the
// query AS THAT USER, so what a screen can read or change is decided by the database's own rules,
// never by anything here. Each function returns `{ ok: true, data }` or `{ ok: false, status, message }`:
// the message is a sentence a person can read (the API's own, or a plain fallback), never a raw error.

import { backendFetch } from './backend';

/** The text of an API error: a string `detail`, or the first problem of a request-model 422. */
export function apiMessage(body, status) {
  const detail = body && body.detail;
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0];
    const where = Array.isArray(first.loc) ? first.loc.filter((p) => p !== 'body' && p !== 'query').join('.') : '';
    return `${where ? `${where}: ` : ''}${first.msg || 'That value is not valid.'}`;
  }
  if (status === 401) return 'Your session has expired. Sign in again.';
  if (status === 403) return 'You do not have permission to do that.';
  if (status === 404) return 'Not found.';
  return 'Something went wrong. Try again.';
}

async function call(path, { method = 'GET', body, query } = {}) {
  const qs = query
    ? '?' + Object.entries(query)
      .filter(([, v]) => v !== undefined && v !== null && v !== '')
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
      .join('&')
    : '';
  try {
    const response = await backendFetch(`/api/v1/safety${path}${qs === '?' ? '' : qs}`, {
      method,
      headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (response.status === 204) return { ok: true, data: null };
    const parsed = await response.json().catch(() => null);
    if (!response.ok) return { ok: false, status: response.status, message: apiMessage(parsed, response.status) };
    return { ok: true, data: parsed };
  } catch {
    return { ok: false, status: 0, message: 'Could not reach the server. Check your connection and try again.' };
  }
}

export const listDecisions = (query) => call('/decisions', { query });
export const getDecision = (id) => call(`/decisions/${encodeURIComponent(id)}`);
export const reviewDecision = (id, state, reason, note) =>
  call(`/decisions/${encodeURIComponent(id)}/review`, { method: 'POST', body: { state, reason: reason || null, note: note || null } });
export const listEvents = (query) => call('/events', { query });
export const getSummary = (days, tz) => call('/summary', { query: { days, tz } });

export const listRules = () => call('/rules');
export const validateRule = (rule) => call('/rules/validate', { method: 'POST', body: rule });
export const createRule = (rule) => call('/rules', { method: 'POST', body: rule });
export const updateRule = (id, patch) => call(`/rules/${encodeURIComponent(id)}`, { method: 'PATCH', body: patch });
export const deleteRule = (id) => call(`/rules/${encodeURIComponent(id)}`, { method: 'DELETE' });

/**
 * Fetch a file from the API and hand it to the browser. Resolves `{ ok, truncated, recorded }` or `{ ok: false, message }`.
 * (`recorded` is false when a served report could not be written to the report history; the file is still downloaded.)
 */
async function saveFile(path, query, fallbackName) {
  try {
    const qs = new URLSearchParams();
    Object.entries(query || {}).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== '') qs.set(k, v); });
    const response = await backendFetch(`/api/v1/safety${path}${qs.toString() ? `?${qs}` : ''}`);
    if (!response.ok) {
      const parsed = await response.json().catch(() => null);
      return { ok: false, message: apiMessage(parsed, response.status) };
    }
    const blob = await response.blob();
    const disposition = response.headers.get('content-disposition') || '';
    const name = (/filename="([^"]+)"/.exec(disposition) || [])[1] || fallbackName;
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10000);
    return { ok: true, truncated: response.headers.get('x-export-truncated') === 'true', recorded: response.headers.get('x-report-recorded') !== 'false', name };
  } catch {
    return { ok: false, message: 'Could not reach the server. Check your connection and try again.' };
  }
}

/** Download the decisions CSV (one row per decision). */
export const downloadExport = ({ since, until, status } = {}) => saveFile('/export', { since, until, status }, 'safety-decisions.csv');

/** The figures of a period: `{ since, until, tz }` (ISO instants, an IANA time zone name). */
export const getReport = ({ since, until, tz }) => call('/report', { query: { since, until, tz } });
/** The report as a file: format 'pdf' or 'csv'. */
export const downloadReport = (format, { since, until, tz, record }) => saveFile('/report/export', { format, since, until, tz, record: record === false ? 'false' : undefined }, `safety-report.${format}`);
/** The reports made before, newest first. */
export const getReportHistory = (limit = 20) => call('/report/history', { query: { limit } });
/** The measured evaluation results (Step 17): the PPE benchmark, the scenario suite and the real-footage run. */
export const getEvaluation = () => call('/evaluation');
/** Every decision and zone event of a period (a shift), oldest first, as a CSV. */
export const downloadAuditTrail = ({ since, until }) => saveFile('/audit-trail', { since, until }, 'safety-audit-trail.csv');
