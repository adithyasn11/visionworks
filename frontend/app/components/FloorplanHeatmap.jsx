'use client';

// frontend/app/components/FloorplanHeatmap.jsx
import React, { useEffect, useRef } from 'react';
import h33 from 'heatmap.js';
import { MapPin } from 'lucide-react';

export const FloorplanHeatmap = () => {
  const heatmapContainerRef = useRef(null);
  const heatmapInstanceRef = useRef(null);

  useEffect(() => {
    if (!heatmapContainerRef.current) return;

    if (!heatmapInstanceRef.current) {
      heatmapInstanceRef.current = h33.create({
        container: heatmapContainerRef.current,
        radius: 40,
        maxOpacity: 0.8,
        minOpacity: 0.1,
        blur: 0.75,
        gradient: {
          '.3': '#38BDF8',
          '.6': '#F59E0B',
          '.95': '#EF4444'
        }
      });
    }

    const mockData = {
      max: 100,
      data: [
        { x: 120, y: 140, value: 85 },
        { x: 125, y: 145, value: 90 },
        { x: 380, y: 150, value: 70 },
        { x: 250, y: 320, value: 40 }
      ]
    };

    heatmapInstanceRef.current.setData(mockData);
  }, []);

  return (
    <div className="glass-panel p-5 flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-slate-200 font-semibold text-sm">
          <MapPin className="w-4 h-4 text-rose-400" />
          <span>2D Top-Down Homography Perspective Heatmap</span>
        </div>
        <span className="text-xs text-slate-400 font-mono">Next.js Canvas Projected</span>
      </div>

      <div
        ref={heatmapContainerRef}
        className="relative w-full h-72 rounded-xl bg-slate-950 border border-slate-800 overflow-hidden"
      >
        <div className="absolute inset-0 border-2 border-dashed border-slate-800 pointer-events-none p-4 grid grid-cols-2 gap-4">
          <div className="border border-slate-800 rounded p-2 text-[10px] text-slate-600 font-mono">Workstation Zone A</div>
          <div className="border border-slate-800 rounded p-2 text-[10px] text-slate-600 font-mono">Workstation Zone B</div>
          <div className="border border-slate-800 rounded p-2 text-[10px] text-slate-600 font-mono">Collaborative Desk</div>
          <div className="border border-slate-800 rounded p-2 text-[10px] text-slate-600 font-mono">Break Lounge</div>
        </div>
      </div>
    </div>
  );
};
