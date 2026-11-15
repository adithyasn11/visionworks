'use client';

// frontend/app/dashboard/safety/DecisionModal.jsx
//
// One flag, opened from a card (Review) or a row (Events): a window that fits the laptop screen, the evidence
// picture large on the left and everything about the flag on the right, with Previous / Next to move through the
// list (arrow keys too) and Escape to close. The picture loads when the window opens (one audited view).

import React, { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { ChevronLeft, ChevronRight, X } from 'lucide-react';
import { workerLabel } from '../../lib/zoneEvents';
import DecisionDetail from './DecisionDetail';
import { SeverityChip, StatusChip, confidenceText, fmtTime } from './common';

export default function DecisionModal({ items, index, onIndex, onClose, role, onChanged }) {
  const d = items[index];

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') { onClose(); return; }
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '');
      if (typing) return;
      if (e.key === 'ArrowRight' && index < items.length - 1) onIndex(index + 1);
      if (e.key === 'ArrowLeft' && index > 0) onIndex(index - 1);
    };
    window.addEventListener('keydown', onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { window.removeEventListener('keydown', onKey); document.body.style.overflow = overflow; };
  }, [index, items.length, onClose, onIndex]);

  if (!d) return null;
  const where = [d.camera && d.camera.name, d.zone && d.zone.name].filter(Boolean).join(' · ');
  const NAV = 'p-2 rounded-lg border border-line text-ink-muted hover:text-ink hover:border-field disabled:opacity-30 disabled:cursor-not-allowed transition-colors';

  // Rendered on <body>: the dashboard's animated sections would otherwise hold this window inside them.
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-3 sm:p-6" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-label={`Flag: ${workerLabel(d.workerKey)}`}
        className="themed w-full max-w-7xl h-[min(92vh,56rem)] flex flex-col rounded-2xl border border-line bg-surface shadow-2xl overflow-hidden">
        <header className="flex flex-wrap items-center gap-x-4 gap-y-2 px-5 py-3 border-b border-line">
          <div className="flex items-center gap-2">
            {d.status === 'POTENTIAL_VIOLATION' ? <SeverityChip severity={d.severity} /> : <StatusChip status={d.status} />}
            <span className="text-[0.9375rem] font-black text-ink">{workerLabel(d.workerKey)}</span>
          </div>
          <span className="text-[0.875rem] text-ink-muted">{where}</span>
          <span className="text-[0.8125rem] font-mono text-ink-faint tabular-nums">{fmtTime(d.observedAt)}</span>
          {confidenceText(d) && <span className="text-[0.8125rem] text-ink-faint">{confidenceText(d)}</span>}
          <div className="ml-auto flex items-center gap-2">
            <span className="text-[0.8125rem] text-ink-faint tabular-nums">{index + 1} of {items.length}</span>
            <button type="button" className={NAV} onClick={() => onIndex(index - 1)} disabled={index === 0} aria-label="Previous flag"><ChevronLeft className="w-4 h-4" /></button>
            <button type="button" className={NAV} onClick={() => onIndex(index + 1)} disabled={index >= items.length - 1} aria-label="Next flag"><ChevronRight className="w-4 h-4" /></button>
            <button type="button" className={NAV} onClick={onClose} aria-label="Close"><X className="w-4 h-4" /></button>
          </div>
          <p className="basis-full text-[0.9375rem] text-ink leading-relaxed">{d.reason}</p>
        </header>
        <div className="flex-1 min-h-0">
          <DecisionDetail key={d.id} decisionId={d.id} role={role} split onChanged={(updated) => onChanged && onChanged(d.id, updated)} />
        </div>
      </div>
    </div>,
    document.body,
  );
}
