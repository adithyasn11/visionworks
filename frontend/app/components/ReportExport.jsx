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


/**
 * Fallback window when no range is supplied.
 *
 * The Reports screen passes `hours` from its range picker, so this is only
 * reached if the component is mounted somewhere that has no picker. It used to
 * be a hardcoded constant that IGNORED the picker entirely — the header said
 * "7d" while the export silently pulled 24 hours, which is worse than having no
 * picker at all: a control that appears to do something and does not.
 */
const DEFAULT_WINDOW_HOURS = 24;

/** The CSV endpoint's own ceiling: `Query(24, ge=1, le=168)` = 7 days. */
const CSV_MAX_HOURS = 168;

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

export const ReportExport = ({ hours = DEFAULT_WINDOW_HOURS, rangeLabel }) => {
  const [busy, setBusy] = useState(null);   // 'csv' | 'pdf' | null
  const [error, setError] = useState(null);

  // Computed once for both the request and the label, so what the panel
  // PROMISES and what the URL ASKS FOR cannot disagree.
  const clamped = hours > CSV_MAX_HOURS;
  const effectiveCsvHours = Math.min(Math.max(1, hours), CSV_MAX_HOURS);
  const clampedLabel =
    effectiveCsvHours >= 24 && effectiveCsvHours % 24 === 0
      ? `last ${effectiveCsvHours / 24}d`
      : `last ${effectiveCsvHours}h`;

  const download = async (kind) => {
    setBusy(kind);
    setError(null);

    // WHAT EACH FORMAT ACTUALLY COVERS — verified against the endpoints in
    // backend/app/api/routers/analytics.py, not assumed:
    //
    //   CSV  takes `hours`, capped at `Query(24, ge=1, le=168)` — SEVEN DAYS.
    //        A longer selection is clamped here rather than sent, because the
    //        API would reject 720 with a validation error the user cannot act
    //        on.
    //   PDF  takes NO window parameter at all. It calls get_analytics_summary()
    //        over the whole history, deliberately, so the executive summary
    //        cannot drift from the dashboard's own totals.
    //
    // The UI states both of these plainly. Sending `hours` to the PDF endpoint
    // would look correct and change nothing — a parameter silently ignored is
    // how an export ends up covering a period nobody chose.
    const path =
      kind === 'csv'
        ? `/api/v1/analytics/report/csv?hours=${effectiveCsvHours}`
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
        Exports contain counts, postures, zones and timestamps only — never video or
        identity.
      </p>

      {/* Each format states its OWN window, because they genuinely differ and
          a single sentence covering both would have to be wrong about one. */}
      <dl className="flex flex-col gap-1.5 rounded-lg border border-line bg-surface-alt px-3.5 py-3">
        <div className="flex items-baseline justify-between gap-3">
          <dt className="text-[11.5px] font-bold text-ink">CSV data</dt>
          <dd className="text-[11px] text-ink-muted font-mono text-right">
            {clampedLabel}
          </dd>
        </div>
        <div className="flex items-baseline justify-between gap-3">
          <dt className="text-[11.5px] font-bold text-ink">Executive PDF</dt>
          <dd className="text-[11px] text-ink-muted font-mono text-right">
            all recorded history
          </dd>
        </div>
      </dl>

      {/* Only shown when the selection was actually clamped, so it reads as an
          answer to "why did I not get 30 days" rather than permanent noise. */}
      {clamped && (
        <p className="text-[11px] text-ink-faint leading-relaxed">
          CSV exports cover at most 7 days. Your {rangeLabel ?? 'selected range'} was
          shortened to fit; the PDF summary still covers everything.
        </p>
      )}

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
