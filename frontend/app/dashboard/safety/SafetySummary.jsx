'use client';

// frontend/app/dashboard/safety/SafetySummary.jsx
//
// The safety numbers for the chosen range, on the overview: how many decisions were compliant, flagged
// or could not be verified, how many flags are still waiting, how many have been judged and how many of those
// a person agreed with, which zone has the most flags, and a bar per day. Counts come from the database
// (exact), in the site's own time zone.

import React, { useEffect, useState } from 'react';
import { Loader2, ShieldCheck } from 'lucide-react';
import { getSummary } from '../../lib/safetyApi';
import { Notice, Panel } from './common';

function Tile({ label, value, hint, strong }) {
  return (
    <div className="rounded-lg border border-line bg-surface-alt/40 px-3 py-2.5">
      <div className="text-[10px] font-mono uppercase tracking-[0.12em] text-ink-faint">{label}</div>
      <div className={`mt-1 text-2xl font-extrabold tabular-nums ${strong ? 'text-accent' : 'text-ink'}`}>{value}</div>
      {hint && <div className="text-[11px] text-ink-faint">{hint}</div>}
    </div>
  );
}

export default function SafetySummary({ days, refreshKey }) {
  const [state, setState] = useState({ loading: true, data: null, error: null });

  useEffect(() => {
    let live = true;
    const tz = (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch { return 'UTC'; } })();
    setState((s) => ({ ...s, loading: true }));
    getSummary(days, tz).then((r) => {
      if (!live) return;
      setState(r.ok ? { loading: false, data: r.data, error: null } : { loading: false, data: null, error: r.message });
    });
    return () => { live = false; };
  }, [days, refreshKey]);

  const d = state.data;
  const share = d && d.confirmed_share_of_reviewed != null ? `${Math.round(Number(d.confirmed_share_of_reviewed) * 100)}%` : null;
  const peak = d ? Math.max(1, ...d.by_day.map((x) => x.decisions)) : 1;
  const label = days === 1 ? 'the last 24 hours' : `the last ${days} days`;

  return (
    <Panel title="Safety" icon={ShieldCheck} aside={label}>
      {state.loading && !d && <p className="flex items-center gap-2 text-xs text-ink-muted"><Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> Loading…</p>}
      {state.error && <Notice tone="error">{state.error}</Notice>}
      {d && d.total === 0 && <p className="text-xs text-ink-muted leading-relaxed">No safety decisions were saved in {label}. Decisions appear here once a run on a registered camera has been saved.</p>}
      {d && d.total > 0 && (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5">
            <Tile label="Compliant" value={d.compliant} hint="entries checked and fine" />
            <Tile label="Potential violations" value={d.potential_violations} hint="flagged for a person to judge" strong={d.potential_violations > 0} />
            <Tile label="Could not verify" value={d.abstentions} hint="the system did not guess" />
            <Tile label="Waiting for review" value={d.open_alerts} hint={d.open_alerts ? 'in the Review queue' : 'nothing waiting'} strong={d.open_alerts > 0} />
          </div>
          <p className="text-xs text-ink-muted">
            {d.confirmed + d.dismissed === 0
              ? 'No flag has been judged yet in this period.'
              : `${d.confirmed + d.dismissed} judged: ${d.confirmed} confirmed, ${d.dismissed} dismissed${share ? ` (people agreed with ${share} of the flags they judged)` : ''}.`}
          </p>
          {d.by_zone[0] && d.by_zone[0].potential_violations > 0 && (
            <p className="text-xs text-ink-muted">Most flags: <span className="text-ink font-semibold">{d.by_zone[0].zone}</span> ({d.by_zone[0].potential_violations}).</p>
          )}
          <div>
            <div className="text-[10px] font-mono uppercase tracking-[0.12em] text-ink-faint mb-1.5">Decisions per day (red: flagged)</div>
            <div className="flex items-end gap-1 h-16" role="img" aria-label={`Decisions per day: ${d.by_day.map((x) => `${x.day} ${x.decisions}`).join(', ')}`}>
              {d.by_day.map((x) => (
                <div key={x.day} className="flex-1 min-w-[6px] flex flex-col justify-end h-full" title={`${x.day}: ${x.decisions} decisions, ${x.potential_violations} flagged`}>
                  <div className="w-full rounded-sm bg-surface-alt border border-line" style={{ height: `${Math.max(x.decisions ? 6 : 2, (x.decisions / peak) * 100)}%` }}>
                    {x.potential_violations > 0 && <div className="w-full rounded-sm bg-accent" style={{ height: `${(x.potential_violations / x.decisions) * 100}%` }} />}
                  </div>
                </div>
              ))}
            </div>
            <div className="flex justify-between text-[10px] text-ink-faint mt-1 tabular-nums"><span>{d.by_day[0] && d.by_day[0].day}</span><span>{d.by_day[d.by_day.length - 1] && d.by_day[d.by_day.length - 1].day}</span></div>
          </div>
        </div>
      )}
    </Panel>
  );
}
