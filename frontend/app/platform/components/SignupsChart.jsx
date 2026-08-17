'use client';

// frontend/app/platform/components/SignupsChart.jsx
//
// Cumulative organisations over the last 30 days, with per-day signups as bars.
//
// TWO SERIES ON PURPOSE. Daily signups alone are almost all zeros at this scale
// and read as a broken chart; a cumulative line alone hides *when* growth
// happened. Together the line shows the trend and the bars show the events.
//
// THEME HANDLING. Recharts needs real colour values, not CSS variables — it
// writes them into SVG attributes where `var()` does not resolve. So the
// computed token values are read from the document after mount and re-read when
// the theme class changes, via MutationObserver on <html>. Without that the
// chart keeps light-mode gridlines after switching to dark.

import React, { useEffect, useState } from 'react';
import {
  ComposedChart, Area, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts';

const FALLBACK = {
  accent: '#DC2626',
  ink: '#0B0A0C',
  inkFaint: '#6B6772',
  line: '#E7E5E9',
  surface: '#FFFFFF',
};

function readTokens() {
  if (typeof window === 'undefined') return FALLBACK;
  const cs = getComputedStyle(document.documentElement);
  const get = (name, fb) => cs.getPropertyValue(name).trim() || fb;
  return {
    accent: get('--accent', FALLBACK.accent),
    ink: get('--ink', FALLBACK.ink),
    inkFaint: get('--ink-faint', FALLBACK.inkFaint),
    line: get('--line', FALLBACK.line),
    surface: get('--surface', FALLBACK.surface),
  };
}

const fmtDay = (iso) => {
  const d = new Date(`${iso}T00:00:00Z`);
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' });
};

function ChartTooltip({ active, payload, label, tokens }) {
  if (!active || !payload?.length) return null;
  const total = payload.find((p) => p.dataKey === 'total')?.value ?? 0;
  const count = payload.find((p) => p.dataKey === 'count')?.value ?? 0;
  return (
    <div
      className="rounded-lg border px-3 py-2 shadow-lg"
      style={{ background: tokens.surface, borderColor: tokens.line }}
    >
      <div className="font-mono text-[10px] uppercase tracking-[0.12em] mb-1" style={{ color: tokens.inkFaint }}>
        {fmtDay(label)}
      </div>
      <div className="text-[13px] font-bold tabular-nums" style={{ color: tokens.ink }}>
        {total} total
      </div>
      <div className="text-[12px] tabular-nums" style={{ color: count > 0 ? tokens.accent : tokens.inkFaint }}>
        {count === 0 ? 'no signups' : `+${count} signup${count > 1 ? 's' : ''}`}
      </div>
    </div>
  );
}

export default function SignupsChart({ data }) {
  const [tokens, setTokens] = useState(FALLBACK);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setTokens(readTokens());
    setMounted(true);

    // Re-read on theme change. The toggle flips a class on <html>, which is not
    // a React state change, so nothing else would trigger a re-render.
    const obs = new MutationObserver(() => setTokens(readTokens()));
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    return () => obs.disconnect();
  }, []);

  const peak = Math.max(1, ...data.map((d) => d.total));

  return (
    <div className="rounded-xl border border-line bg-surface p-4 sm:p-5 themed">
      <div className="flex items-baseline justify-between gap-4 mb-4">
        <div>
          <h2 className="text-[14px] font-bold tracking-tight text-ink">Organisations over time</h2>
          <p className="text-[11.5px] text-ink-faint mt-0.5">Last 30 days · cumulative</p>
        </div>
        <div className="text-right shrink-0">
          <div className="text-[22px] font-black tracking-tight tabular-nums leading-none text-ink">
            {peak}
          </div>
          <div className="font-mono text-[9.5px] uppercase tracking-[0.14em] text-ink-faint mt-1">
            total
          </div>
        </div>
      </div>

      {/* Fixed height so the card does not jump between server and client
          render — ResponsiveContainer needs a sized parent. */}
      <div className="h-[180px] -ml-2">
        {mounted ? (
          <ResponsiveContainer width="100%" height="100%">
            <ComposedChart data={data} margin={{ top: 4, right: 6, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id="signupFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={tokens.accent} stopOpacity={0.22} />
                  <stop offset="100%" stopColor={tokens.accent} stopOpacity={0.02} />
                </linearGradient>
              </defs>

              <CartesianGrid stroke={tokens.line} strokeDasharray="3 3" vertical={false} />

              <XAxis
                dataKey="date"
                tickFormatter={fmtDay}
                stroke={tokens.line}
                tick={{ fill: tokens.inkFaint, fontSize: 10.5 }}
                tickLine={false}
                axisLine={{ stroke: tokens.line }}
                // Every-5th-day labels: 30 dates overlap at this width.
                interval={4}
                minTickGap={8}
              />
              <YAxis
                stroke={tokens.line}
                tick={{ fill: tokens.inkFaint, fontSize: 10.5 }}
                tickLine={false}
                axisLine={false}
                width={30}
                allowDecimals={false}
                domain={[0, (max) => Math.max(2, Math.ceil(max * 1.25))]}
              />

              <Tooltip
                content={<ChartTooltip tokens={tokens} />}
                cursor={{ fill: tokens.line, fillOpacity: 0.35 }}
              />

              {/* Bars first so the line and its fill sit on top. */}
              <Bar dataKey="count" fill={tokens.accent} fillOpacity={0.55} radius={[2, 2, 0, 0]} maxBarSize={10} />
              <Area
                type="monotone"
                dataKey="total"
                stroke={tokens.accent}
                strokeWidth={2}
                fill="url(#signupFill)"
                dot={false}
                activeDot={{ r: 3.5, fill: tokens.accent, stroke: tokens.surface, strokeWidth: 2 }}
              />
            </ComposedChart>
          </ResponsiveContainer>
        ) : (
          <div className="h-full w-full rounded-lg bg-surface-alt animate-pulse" />
        )}
      </div>
    </div>
  );
}
