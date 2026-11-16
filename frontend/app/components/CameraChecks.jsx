'use client';

// frontend/app/components/CameraChecks.jsx
//
// The Live feed's side column before a run: what this camera will be checked for, its zones and its rules.

import React, { useEffect, useState } from 'react';
import { CalendarDays, ListChecks, MapPin } from 'lucide-react';
import { listRules, listDecisions } from '../lib/safetyApi';
import { listCameras } from '../lib/platform/cameras';
import { listSafetyZones } from '../lib/safety/management';
import { ruleTypeMeta } from '../lib/safety/ruleTypes';

const ZONE_TYPE = { PPE_CHECKPOINT: 'PPE checkpoint', APPROACH: 'Approach', RESTRICTED: 'Restricted', EXIT: 'Exit', GENERAL: 'General' };

function Section({ icon: Icon, title, count, children, grow = false }) {
  return (
    <section className={`rounded-xl border border-line bg-surface p-3 flex flex-col gap-2 ${grow ? 'lg:flex-1 lg:min-h-[8rem]' : ''}`}>
      <header className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-semibold text-ink">
          <Icon className="w-4 h-4 text-accent" aria-hidden="true" /> {title}
        </span>
        <span className="text-[0.75rem] text-ink-faint tabular-nums">{count}</span>
      </header>
      {children}
    </section>
  );
}

export default function CameraChecks({ cameraId }) {
  const [data, setData] = useState(null);
  const [today, setToday] = useState(null);
  useEffect(() => {
    let live = true;
    Promise.all([listSafetyZones(), listRules()]).then(([z, r]) => {
      if (!live) return;
      const zones = z.zones || [];
      const camOf = new Map(zones.map((x) => [x.id, x.camera]));
      const mine = zones.filter((x) => x.camera === cameraId);
      const rules = (r.ok ? r.data.rules || [] : []).filter((x) => {
        const cam = (x.camera && x.camera.name) || camOf.get(x.zoneId) || null;
        return cam ? cam === cameraId : !x.zoneId;          // a rule with no camera or zone runs everywhere
      });
      setData({ zones: mine, rules });
    });
    // Today on this camera: how many people were checked, how many flagged, and when last.
    listCameras().then(async (c) => {
      const cam = (c.cameras || []).find((x) => x.name === cameraId);
      if (!cam || !live) { if (live) setToday(false); return; }
      const start = new Date(); start.setHours(0, 0, 0, 0);
      const since = start.toISOString();
      const [all, flagged] = await Promise.all([
        listDecisions({ camera_id: cam.id, since, limit: 1 }),
        listDecisions({ camera_id: cam.id, since, status: 'POTENTIAL_VIOLATION', limit: 1 }),
      ]);
      if (!live) return;
      setToday(all.ok ? {
        checked: all.data.total ?? all.data.decisions.length,
        flagged: flagged.ok ? (flagged.data.total ?? flagged.data.decisions.length) : 0,
        last: (all.data.decisions[0] || {}).observedAt || null,
      } : false);
    });
    return () => { live = false; };
  }, [cameraId]);

  if (!data) return null;
  const lastAt = today && today.last ? new Date(today.last).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : null;
  return (
    <>
      {today && (
        <Section icon={CalendarDays} title="Today" count={lastAt ? `last ${lastAt}` : ''}>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <p className="text-xl font-black text-ink tabular-nums leading-none">{today.checked}</p>
              <p className="mt-1 text-[0.75rem] font-bold text-ink-muted">Checked</p>
            </div>
            <div>
              <p className={`text-xl font-black tabular-nums leading-none ${today.flagged ? 'text-accent' : 'text-ink'}`}>{today.flagged}</p>
              <p className="mt-1 text-[0.75rem] font-bold text-ink-muted">Flagged</p>
            </div>
          </div>
        </Section>
      )}
      <Section icon={MapPin} title="Zones" count={data.zones.length}>
        {data.zones.length === 0 ? (
          <p className="text-[0.8125rem] text-ink-muted">None on this camera yet.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-line-soft">
            {data.zones.map((z) => (
              <li key={z.id} className="flex items-center justify-between gap-2 py-1.5 text-[0.8125rem]">
                <span className="text-ink font-medium truncate">{z.name}</span>
                <span className="text-[0.75rem] text-ink-faint shrink-0">{ZONE_TYPE[z.safetyType] || 'Zone'}</span>
              </li>
            ))}
          </ul>
        )}
      </Section>
      <Section grow icon={ListChecks} title="Rules" count={data.rules.filter((x) => x.isEnabled !== false).length}>
        {data.rules.length === 0 ? (
          <p className="text-[0.8125rem] text-ink-muted">No rules yet.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-line-soft">
            {data.rules.map((x) => (
              <li key={x.id} className={`flex flex-col py-1.5 text-[0.8125rem] ${x.isEnabled === false ? 'opacity-50' : ''}`}>
                <span className="text-ink font-medium truncate">{(ruleTypeMeta(x.type) || {}).label || x.name}</span>
                <span className="text-[0.75rem] text-ink-faint truncate">
                  {x.isEnabled === false ? 'Off' : (x.zone && x.zone.name) || 'Whole camera'}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>
    </>
  );
}
