'use client';

// frontend/app/components/AnalyticsCharts.jsx
import React from 'react';
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  PieChart,
  Pie,
  Cell,
  BarChart,
  Bar,
  CartesianGrid
} from 'recharts';
import { TrendingUp, PieChart as PieIcon, BarChart3 } from 'lucide-react';

const mockTimeline = [
  { time: '09:00', score: 62 },
  { time: '10:00', score: 78 },
  { time: '11:00', score: 85 },
  { time: '12:00', score: 45 },
  { time: '13:00', score: 72 },
  { time: '14:00', score: 88 },
  { time: '15:00', score: 81 },
  { time: '16:00', score: 74 }
];

const mockPosturePie = [
  { name: 'Sitting', value: 65, color: '#22C55E' },
  { name: 'Standing', value: 25, color: '#38BDF8' },
  { name: 'Walking', value: 10, color: '#F59E0B' }
];

const mockZoneDwell = [
  { zone: 'Desk 1', minutes: 142 },
  { zone: 'Desk 2', minutes: 185 },
  { zone: 'Meeting A', minutes: 64 },
  { zone: 'Lounge', minutes: 35 }
];

export const AnalyticsCharts = () => {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
      {/* 1. Activity Score Timeline */}
      <div className="glass-panel p-5 flex flex-col gap-4 md:col-span-2">
        <div className="flex items-center gap-2 text-slate-200 font-semibold text-sm">
          <TrendingUp className="w-4 h-4 text-sky-400" />
          <span>Supabase Telemetry Activity Index Timeline (0 - 100 Score)</span>
        </div>
        <div className="h-64 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={mockTimeline}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis dataKey="time" stroke="#94A3B8" />
              <YAxis domain={[0, 100]} stroke="#94A3B8" />
              <Tooltip
                contentStyle={{ backgroundColor: '#1E293B', borderColor: '#475569', borderRadius: '8px' }}
                itemStyle={{ color: '#38BDF8' }}
              />
              <Line
                type="monotone"
                dataKey="score"
                stroke="#38BDF8"
                strokeWidth={3}
                dot={{ fill: '#38BDF8', r: 5 }}
                activeDot={{ r: 8 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* 2. Posture Distribution Pie */}
      <div className="glass-panel p-5 flex flex-col gap-4">
        <div className="flex items-center gap-2 text-slate-200 font-semibold text-sm">
          <PieIcon className="w-4 h-4 text-emerald-400" />
          <span>Posture Balance Ratio</span>
        </div>
        <div className="h-56 w-full flex items-center justify-center">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={mockPosturePie}
                cx="50%"
                cy="50%"
                innerRadius={60}
                outerRadius={80}
                paddingAngle={5}
                dataKey="value"
              >
                {mockPosturePie.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip contentStyle={{ backgroundColor: '#1E293B', borderColor: '#475569' }} />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div className="flex justify-around text-xs font-medium text-slate-300">
          {mockPosturePie.map((item) => (
            <div key={item.name} className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: item.color }} />
              <span>{item.name} ({item.value}%)</span>
            </div>
          ))}
        </div>
      </div>

      {/* 3. Zone Dwell Time Bar Chart */}
      <div className="glass-panel p-5 flex flex-col gap-4">
        <div className="flex items-center gap-2 text-slate-200 font-semibold text-sm">
          <BarChart3 className="w-4 h-4 text-amber-400" />
          <span>Workstation Dwell Time (Minutes)</span>
        </div>
        <div className="h-56 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={mockZoneDwell}>
              <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
              <XAxis dataKey="zone" stroke="#94A3B8" />
              <YAxis stroke="#94A3B8" />
              <Tooltip contentStyle={{ backgroundColor: '#1E293B', borderColor: '#475569' }} />
              <Bar dataKey="minutes" fill="#F59E0B" radius={[6, 6, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
};
