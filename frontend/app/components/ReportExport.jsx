'use client';

// frontend/app/components/ReportExport.jsx
//
// Download the recorded telemetry as a CSV extract or an executive PDF.
//
// Both files are generated server-side from `activity_logs` (see
// backend/app/utils/report_generator.py) and streamed back as attachments, so
// what lands in the user's Downloads folder is the same data the dashboard
// charts are drawn from — not a client-side re-derivation that could drift.
//
// The backend answers 404 with an explanatory message when there is nothing to
// export yet. That is surfaced verbatim rather than as "download failed",
// because "no one has been observed yet" is a normal first-run state, not an
// error the user needs to debug.

import React, { useState } from 'react';
import { FileDown, FileSpreadsheet, FileText, Loader2, AlertCircle } from 'lucide-react';
import { backendFetch } from '../lib/backend';


/** Matches the window the dashboard charts use, so exports agree with them. */
const WINDOW_HOURS = 24;

/**
 * Pulls the filename the server chose out of Content-Disposition.
 *
 * The server stamps each export with a timestamp, so honouring its name keeps
 * successive downloads distinguishable instead of collapsing into
 * "report (1).csv", "report (2).csv".
 */
function filenameFrom(response, fallback) {
  const header = response.headers.get('content-disposition') || '';
  const match = /filename="?([^";]+)"?/i.exec(header);
  return match ? match[1] : fallback;
}

export const ReportExport = () => {
  const [busy, setBusy] = useState(null);   // 'csv' | 'pdf' | null
  const [error, setError] = useState(null);

  const download = async (kind) => {
    setBusy(kind);
    setError(null);

    const path =
      kind === 'csv'
        ? `/api/v1/analytics/report/csv?hours=${WINDOW_HOURS}`
        : '/api/v1/analytics/report/pdf';

    let objectUrl;
    try {
      const res = await backendFetch(path);

      if (!res.ok) {
        // The API explains *why* there is nothing to export; prefer that over a
        // generic status code.
        const detail = await res.json().catch(() => null);
        throw new Error(detail?.detail || `Export failed (${res.status})`);
      }

      const blob = await res.blob();
      objectUrl = URL.createObjectURL(blob);

      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = filenameFrom(res, `workplace_report.${kind}`);
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    } catch (err) {
      setError(
        /failed to fetch|networkerror|load failed/i.test(String(err?.message))
          ? 'Cannot reach the backend. Start the FastAPI server on port 8001.'
          : String(err?.message || err),
      );
    } finally {
      // Revoking immediately can cancel the download in some browsers, so give
      // the click a moment to be picked up first.
      if (objectUrl) setTimeout(() => URL.revokeObjectURL(objectUrl), 10000);
      setBusy(null);
    }
  };

  return (
    <div className="glass-panel p-5 flex flex-col gap-4">
      <div className="flex items-center gap-2 text-ink font-bold text-[13.5px] tracking-tight">
        <FileDown className="w-4 h-4 text-accent" />
        <span>Export Report</span>
      </div>

      <p className="text-[11.5px] text-ink-muted leading-relaxed">
        Download the last {WINDOW_HOURS} hours of recorded telemetry. Exports contain
        counts, postures, zones and timestamps only — never video or identity.
      </p>

      <div className="flex items-center gap-2.5 flex-wrap">
        <button
          type="button"
          onClick={() => download('csv')}
          disabled={busy !== null}
          className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg bg-surface-alt hover:bg-surface border border-line hover:border-field text-ink text-xs font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {busy === 'csv'
            ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
            : <FileSpreadsheet className="w-3.5 h-3.5 text-accent" />}
          CSV data
        </button>

        <button
          type="button"
          onClick={() => download('pdf')}
          disabled={busy !== null}
          className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg bg-accent hover:brightness-110 text-white text-xs font-bold transition-[filter] disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {busy === 'pdf'
            ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
            : <FileText className="w-3.5 h-3.5" />}
          Executive PDF
        </button>
      </div>

      {error && (
        <div className="flex items-start gap-2 text-[11.5px] text-accent leading-relaxed" role="alert">
          <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}
    </div>
  );
};
