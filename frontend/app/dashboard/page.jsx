'use client';

// frontend/app/dashboard/page.jsx
import React, { useState, useEffect } from 'react';
import { Header } from '../components/Header';
import { VideoCanvasPlayer } from '../components/VideoCanvasPlayer';
import { AnalyticsCharts } from '../components/AnalyticsCharts';
import { FloorplanHeatmap } from '../components/FloorplanHeatmap';
import { ZoneEditor } from '../components/ZoneEditor';
import { ReportExport } from '../components/ReportExport';
import { SupabaseModal } from '../components/SupabaseModal';
import { createClient } from '@supabase/supabase-js';

const BACKEND_WS = 'ws://localhost:8001';

/** The camera these dashboard zones belong to; matches the backend's id for
 *  the browser/live camera path. */
const CAMERA_ID = 'live_webcam';

/**
 * Section heading for a band of panels.
 *
 * Same typographic system as the founder console: a mono eyebrow in the accent,
 * a bold title, and one line of plain-language context. The hairline underneath
 * separates bands without adding another boxed container.
 */
function SectionLabel({ id, title, children }) {
  return (
    <div className="border-b border-line pb-3">
      <h2 id={id} className="text-[15px] font-black tracking-tight text-ink">
        {title}
      </h2>
      <p className="mt-1 text-[12.5px] text-ink-muted leading-relaxed">{children}</p>
    </div>
  );
}

export default function Dashboard() {
  const [isConnected, setIsConnected]       = useState(false);
  const [supabaseModal, setSupabaseModal]   = useState(false);
  const [supabaseClient, setSupabaseClient] = useState(null);

  // Zones come from the database now, drawn by the user in ZoneEditor, rather
  // than from a hardcoded pair of rectangles. The CV pipeline reads the same
  // rows, so what is drawn here is what occupancy is actually attributed to.
  const [activeZones, setActiveZones] = useState([]);

  const handleConnectSupabase = ({ url, key }) => {
    if (!url || !key) return;
    try {
      const client = createClient(url, key);
      setSupabaseClient(client);
      setSupabaseModal(false);
    } catch (e) {
      console.error('Supabase client init failed:', e);
    }
  };

  // Save incoming frame data to Supabase activity_logs
  const handleFrameData = async (data) => {
    if (!supabaseClient || !data.tracked_entities?.length) return;

    const rows = data.tracked_entities.map((e) => ({
      camera_id: 'uploaded_video',
      zone_id: e.zone_id || 'TRANSIT_ZONE',
      track_id: e.track_id,
      posture_state: e.posture,
      activity_score: e.activity_score,
      dwell_duration_seconds: e.dwell_duration_seconds || 0,
    }));

    const { error } = await supabaseClient.from('activity_logs').insert(rows);
    if (error) console.error('Supabase insert error:', error.message);
  };

  // Also maintain a live WebSocket to /ws/stream for camera-based feeds (optional)
  useEffect(() => {
    const ws = new WebSocket(`${BACKEND_WS}/api/v1/ws/stream/cam_floor_01`);
    ws.onopen  = () => setIsConnected(true);
    ws.onclose = () => setIsConnected(false);
    ws.onerror = () => setIsConnected(false);
    return () => ws.close();
  }, []);

  // dashboard-shell paints the dark control-room ground its panels expect.
  return (
    <div className="dashboard-shell">
      <div className="max-w-7xl mx-auto p-4 md:p-6 min-h-screen flex flex-col gap-6">
      <Header
        isConnected={isConnected}
        onOpenSupabaseModal={() => setSupabaseModal(true)}
      />

      {/* Three bands, ordered by how the page is actually used: watch the feed,
          configure what it measures, then read what it measured. Each band gets
          a rule and a label so the page scans as sections rather than as a wall
          of equally-weighted panels. */}
      <main className="flex flex-col gap-8">

        {/* ── Live monitoring ── */}
        <section aria-labelledby="sec-live" className="flex flex-col gap-4">
          <SectionLabel id="sec-live" title="Live monitoring">
            Detection, tracking and posture, running on the current feed.
          </SectionLabel>

          <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] gap-5 items-start">
            <VideoCanvasPlayer
              activeZones={activeZones}
              onFrameData={handleFrameData}
            />
            <FloorplanHeatmap />
          </div>
        </section>

        {/* ── Configuration ── */}
        <section aria-labelledby="sec-config" className="flex flex-col gap-4">
          <SectionLabel id="sec-config" title="Configuration">
            Define the areas occupancy is attributed to, and export what has been recorded.
          </SectionLabel>

          <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] gap-5 items-start">
            <ZoneEditor cameraId={CAMERA_ID} onZonesChanged={setActiveZones} />
            <ReportExport />
          </div>
        </section>

        {/* ── Analytics ── */}
        <section aria-labelledby="sec-analytics" className="flex flex-col gap-4">
          <SectionLabel id="sec-analytics" title="Analytics">
            Aggregated from recorded telemetry — counts, postures and dwell time only.
          </SectionLabel>

          <AnalyticsCharts />
        </section>
      </main>

      <SupabaseModal
        isOpen={supabaseModal}
        onClose={() => setSupabaseModal(false)}
        onSave={handleConnectSupabase}
      />

        <footer className="mt-4 text-center text-xs text-ink-faint py-4 border-t border-line">
          Vision-Based Workplace Activity Analytics System • Next.js + FastAPI + Supabase • Major Project
        </footer>
      </div>
    </div>
  );
}
