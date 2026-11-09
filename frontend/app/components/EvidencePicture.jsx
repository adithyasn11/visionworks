'use client';

// frontend/app/components/EvidencePicture.jsx
//
// "View evidence" for one flagged decision: the picture the system kept, with faces
// blurred, opened on demand. Loading it is an audited act on the server, so nothing is
// fetched until the button is pressed, and the picture is released when it is closed.

import React, { useEffect, useRef, useState } from 'react';
import { Image as ImageIcon, X } from 'lucide-react';
import { loadEvidence } from '../lib/evidence';

export default function EvidencePicture({ decisionId }) {
  const [state, setState] = useState({ open: false, loading: false, url: null, error: null });
  // Which request is current. A reply that arrives after the picture was closed (or the row
  // went away) is dropped, and its object URL released, rather than reopening the picture.
  const request = useRef(0);

  // Release the object URL when the picture is closed or replaced.
  useEffect(() => () => { if (state.url) URL.revokeObjectURL(state.url); }, [state.url]);
  // Escape closes the picture, like any other pop-up.
  useEffect(() => {
    if (!state.open) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') { request.current += 1; setState({ open: false, loading: false, url: null, error: null }); } };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [state.open]);
  // Invalidate any request still in flight when the row goes away.
  useEffect(() => () => { request.current += 1; }, []);

  async function open() {
    if (state.loading) return;                      // a second click must not make a second (audited) request
    const mine = ++request.current;
    setState({ open: true, loading: true, url: null, error: null });
    const result = await loadEvidence(decisionId);
    if (mine !== request.current) {
      if (result.url) URL.revokeObjectURL(result.url);
      return;
    }
    setState({ open: true, loading: false, url: result.url || null, error: result.error || null });
  }

  function close() {
    request.current += 1;
    setState({ open: false, loading: false, url: null, error: null });
  }

  if (!state.open) {
    return (
      <button
        type="button"
        onClick={open}
        className="inline-flex items-center gap-1.5 self-start rounded-md border border-line px-2 py-1 text-[11px] font-medium text-ink hover:border-[color:var(--accent)] hover:text-accent focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--accent)]"
      >
        <ImageIcon className="w-3.5 h-3.5" aria-hidden="true" />
        View evidence picture
      </button>
    );
  }

  return (
    <figure className="flex flex-col gap-1.5 rounded-lg border border-line bg-surface-alt p-2" aria-live="polite">
      <div className="flex items-center justify-between gap-2">
        <figcaption className="text-[10px] font-mono uppercase tracking-[0.12em] text-ink-faint">
          Evidence picture · faces blurred
        </figcaption>
        <button
          type="button"
          onClick={close}
          aria-label="Close the evidence picture"
          className="rounded p-0.5 text-ink-muted hover:text-ink focus:outline-none focus-visible:ring-1 focus-visible:ring-[color:var(--accent)]"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
      {state.loading && <p className="text-xs text-ink-muted">Loading…</p>}
      {state.error && <p className="text-xs text-ink-muted">{state.error}</p>}
      {state.url && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={state.url}
          alt="The frame the system flagged, with faces blurred and the person, zone and detected helmet and vest outlined"
          className="w-full rounded-md border border-line"
        />
      )}
    </figure>
  );
}
