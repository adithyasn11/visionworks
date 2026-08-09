'use client';

// frontend/app/dashboard/page.jsx
import React, { useState, useEffect } from 'react';
import { Header } from '../components/Header';
import { VideoCanvasPlayer } from '../components/VideoCanvasPlayer';
import { AnalyticsCharts } from '../components/AnalyticsCharts';
import { FloorplanHeatmap } from '../components/FloorplanHeatmap';
import { SupabaseModal } from '../components/SupabaseModal';
import { createClient } from '@supabase/supabase-js';

const BACKEND_WS = 'ws://localhost:8001';

export default function Dashboard() {
  const [isConnected, setIsConnected]       = useState(false);
  const [supabaseModal, setSupabaseModal]   = useState(false);
  const [supabaseClient, setSupabaseClient] = useState(null);

  const activeZones = [
    {
      zone_id: 'workstation_01',
      zone_name: 'Workstation 1',
      polygon_coordinates: [[100, 60], [480, 60], [480, 480], [100, 480]]
    },
    {
      zone_id: 'workstation_02',
      zone_name: 'Workstation 2',
      polygon_coordinates: [[500, 60], [900, 60], [900, 480], [500, 480]]
    }
  ];

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

  return (
    <div className="max-w-7xl mx-auto p-4 md:p-6 min-h-screen flex flex-col gap-6">
      <Header
        isConnected={isConnected}
        onOpenSupabaseModal={() => setSupabaseModal(true)}
      />

      <main className="flex flex-col gap-6">
        {/* Video Player + Heatmap */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2">
            <VideoCanvasPlayer
              activeZones={activeZones}
              onFrameData={handleFrameData}
            />
          </div>
          <div className="lg:col-span-1">
            <FloorplanHeatmap />
          </div>
        </div>

        <AnalyticsCharts />
      </main>

      <SupabaseModal
        isOpen={supabaseModal}
        onClose={() => setSupabaseModal(false)}
        onSave={handleConnectSupabase}
      />

      <footer className="mt-4 text-center text-xs text-slate-500 py-4 border-t border-slate-200">
        Vision-Based Workplace Activity Analytics System • Next.js + FastAPI + Supabase • Major Project
      </footer>
    </div>
  );
}
