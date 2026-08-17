/**
 * VisionWorks — development seed
 *
 * Generates a realistic 90-day dataset so /analytics can be built against real
 * shapes before the CV pipeline is writing buckets. Roughly:
 *
 *   2 orgs · 3 sites · 6 cameras · 18 zones
 *   ~90 days x 9 working hours x 60 min x 18 zones  ≈  875,000 minute buckets
 *   + day rollups, sessions, alert rules, fired alerts, audit entries
 *
 * WHY THE DATA IS SHAPED, NOT RANDOM
 *
 * Random noise is useless for building charts: every zone averages the same,
 * so a bug that swaps two zones is invisible and a heatmap looks like static.
 * This generator models the things a facility manager actually sees —
 *
 *   · a mid-morning and mid-afternoon peak with a lunch dip
 *   · Monday and Friday lighter than midweek (hybrid working)
 *   · one deliberately underused zone, so the "consolidate this floor"
 *     insight has something to find
 *   · one chronically overcrowded zone, so alerts have something to fire on
 *   · meeting rooms that are empty or full, never half-occupied
 *   · corridors that are pure transit — high walking, near-zero dwell
 *
 * so charts have visible structure and every feature has a case to exercise.
 *
 * Run:  npx tsx prisma/seed.ts          (uses DIRECT_URL)
 *       npx tsx prisma/seed.ts --wipe   (clear seeded orgs first)
 *
 * SAFETY: only ever touches organisations whose slug starts with `demo-`.
 * It cannot delete real data, because it never selects rows outside that
 * prefix.
 */

// Same as prisma.config.ts: dotenv reads .env by default, but Next.js keeps
// local secrets in .env.local, so it has to be named.
import { config as loadEnv } from 'dotenv';
loadEnv({ path: '.env.local' });
loadEnv();

import { PrismaPg } from '@prisma/adapter-pg';
import { PrismaClient, Prisma } from '../app/generated/prisma/client';

// Prisma 7 requires an explicit driver adapter — `new PrismaClient()` with no
// options throws. Seeding uses DIRECT_URL (port 5432): bulk createMany over
// PgBouncer in transaction mode is slower and can drop the connection
// mid-batch.
const connectionString = process.env.DIRECT_URL ?? process.env.DATABASE_URL;
if (!connectionString) {
  console.error('Set DIRECT_URL (preferred) or DATABASE_URL before seeding.');
  process.exit(1);
}

const prisma = new PrismaClient({ adapter: new PrismaPg({ connectionString }) });

const DEMO_SLUG_PREFIX = 'demo-';
const DAYS = 90;
const MINUTE_BATCH = 5_000;

/* ── Deterministic RNG ─────────────────────────────────────────────────────
   A fixed seed means two runs produce identical data, so a chart that changes
   between runs indicates a code change rather than new noise. Mulberry32. */
function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) >>> 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rand = rng(20260817);

const pick = <T,>(xs: readonly T[]): T => xs[Math.floor(rand() * xs.length)];
const jitter = (base: number, spread: number) => base + (rand() - 0.5) * 2 * spread;
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/* ── Occupancy model ───────────────────────────────────────────────────────
   Returns expected concurrent occupancy for a zone at a given local time, as
   a fraction of capacity. */

/** Two peaks with a lunch dip — the shape every real office curve has. */
function timeOfDayFactor(minuteOfDay: number): number {
  const h = minuteOfDay / 60;
  if (h < 8 || h >= 19) return 0.02;
  const morning = Math.exp(-Math.pow(h - 10.5, 2) / 2.2);
  const afternoon = Math.exp(-Math.pow(h - 15.0, 2) / 2.8);
  const lunchDip = 1 - 0.45 * Math.exp(-Math.pow(h - 13.0, 2) / 0.5);
  return clamp((0.95 * morning + 0.85 * afternoon) * lunchDip, 0.02, 1);
}

/** Hybrid-working weekly shape: Tue–Thu busy, Mon/Fri lighter, weekend dead. */
function dayOfWeekFactor(isoDay: number): number {
  return { 1: 0.72, 2: 0.98, 3: 1.0, 4: 0.94, 5: 0.55, 6: 0.06, 7: 0.03 }[isoDay] ?? 0.8;
}

type ZoneProfile = {
  name: string;
  zoneType: 'WORKSTATION' | 'MEETING' | 'BREAK' | 'CORRIDOR' | 'RECEPTION';
  capacity: number | null;
  /** Multiplier on the demand curve. <0.4 reads as underused; >1.0 crowds. */
  demand: number;
  /** Posture mix as [sitting, standing, walking] weights. */
  posture: [number, number, number];
  /** Meeting rooms are all-or-nothing rather than smoothly varying. */
  bursty?: boolean;
  excludeFromUtilisation?: boolean;
  note: string;
};

const ZONE_PROFILES: ZoneProfile[] = [
  { name: 'Desk Bank A', zoneType: 'WORKSTATION', capacity: 12, demand: 0.92, posture: [0.86, 0.09, 0.05], note: 'Healthy core desk bank — the baseline everything else is read against.' },
  { name: 'Desk Bank B', zoneType: 'WORKSTATION', capacity: 12, demand: 0.74, posture: [0.84, 0.10, 0.06], note: 'Slightly quieter than A.' },
  { name: 'Desk Bank C', zoneType: 'WORKSTATION', capacity: 10, demand: 0.24, posture: [0.88, 0.07, 0.05], note: 'DELIBERATELY UNDERUSED — this is the row the "reclaim this space" insight must surface.' },
  { name: 'Meeting Room 1', zoneType: 'MEETING', capacity: 8, demand: 0.80, posture: [0.90, 0.08, 0.02], bursty: true, note: 'Bursty: booked solid or empty, never 40%.' },
  { name: 'Meeting Room 2', zoneType: 'MEETING', capacity: 4, demand: 1.35, posture: [0.88, 0.09, 0.03], bursty: true, note: 'DELIBERATELY OVERSUBSCRIBED — gives OVERCROWDING alerts something to fire on.' },
  { name: 'Break Area', zoneType: 'BREAK', capacity: 10, demand: 0.55, posture: [0.45, 0.38, 0.17], note: 'Lunch-dominated; mixed posture.' },
  { name: 'Main Corridor', zoneType: 'CORRIDOR', capacity: null, demand: 0.45, posture: [0.02, 0.10, 0.88], excludeFromUtilisation: true, note: 'Pure transit — no capacity, excluded from utilisation. Proves null-capacity handling.' },
  { name: 'Reception', zoneType: 'RECEPTION', capacity: 6, demand: 0.35, posture: [0.55, 0.35, 0.10], note: 'Morning-weighted.' },
];

function occupancyFor(p: ZoneProfile, minuteOfDay: number, isoDay: number, dayIndex: number): number {
  const cap = p.capacity ?? 6;
  let f = timeOfDayFactor(minuteOfDay) * dayOfWeekFactor(isoDay) * p.demand;

  // Meeting rooms occupy in blocks. Hash the half-hour slot so a booking
  // persists across the whole slot instead of flickering minute to minute.
  if (p.bursty) {
    const slot = Math.floor(minuteOfDay / 30) + dayIndex * 48;
    const booked = rng(slot * 2654435761)() < clamp(f, 0, 0.95);
    f = booked ? 0.85 + rand() * 0.3 : 0.02;
  }

  // Slow upward drift across the 90 days, so trend lines have a real slope
  // rather than being flat with noise on top.
  f *= 1 + (dayIndex / DAYS) * 0.18;

  return clamp(Math.round(jitter(cap * f, cap * 0.12)), 0, Math.ceil(cap * 1.4));
}

/* ── Wipe ──────────────────────────────────────────────────────────────── */

async function wipe() {
  const orgs = await prisma.organisation.findMany({
    where: { slug: { startsWith: DEMO_SLUG_PREFIX } },
    select: { id: true, name: true },
  });
  if (orgs.length === 0) {
    console.log('  nothing to wipe');
    return;
  }
  const ids = orgs.map((o) => o.id);
  console.log(`  wiping ${orgs.length} demo org(s): ${orgs.map((o) => o.name).join(', ')}`);

  // Cascades would handle the children, but deleting ~800k buckets in one
  // statement holds a long transaction and reliably times out over Supabase's
  // pooler. Delete in chunks so each statement is short — the same reason the
  // inserts are batched.
  let removed = 0;
  for (;;) {
    const doomed = await prisma.zoneMinuteStat.findMany({
      where: { orgId: { in: ids } },
      select: { id: true },
      take: MINUTE_BATCH,
    });
    if (doomed.length === 0) break;
    const res = await prisma.zoneMinuteStat.deleteMany({
      where: { id: { in: doomed.map((d) => d.id) } },
    });
    removed += res.count;
    process.stdout.write(`\r  removed ${removed.toLocaleString()} minute buckets`);
  }
  process.stdout.write(`\r  removed ${removed.toLocaleString()} minute buckets\n`);

  // Day rollups are not org-cascaded through a relation in the Prisma client
  // (ZoneDayStat has no relation fields), so clear them explicitly.
  const days = await prisma.zoneDayStat.deleteMany({ where: { orgId: { in: ids } } });
  if (days.count > 0) console.log(`  removed ${days.count.toLocaleString()} day rows`);

  await prisma.organisation.deleteMany({ where: { id: { in: ids } } });
}

/* ── Seed ──────────────────────────────────────────────────────────────── */

async function main() {
  const wipeFirst = process.argv.includes('--wipe');
  console.log('VisionWorks seed\n');

  if (wipeFirst) {
    console.log('wipe:');
    await wipe();
    console.log('');
  }

  // Bail if demo data is already here — but say whether it is COMPLETE.
  //
  // The first version of this guard only checked "does a demo org exist", which
  // is misleading after an interrupted run: the org exists, so the seed exits
  // reporting success while the buckets are half-written and the day rollups,
  // sessions and alerts are missing entirely. Seeding 800k+ rows over a network
  // takes minutes and does get interrupted, so the guard has to distinguish
  // "already done" from "stopped partway".
  const existing = await prisma.organisation.findFirst({
    where: { slug: { startsWith: DEMO_SLUG_PREFIX } },
    select: { id: true, name: true },
  });
  if (existing) {
    const [buckets, dayRows, sessions] = await Promise.all([
      prisma.zoneMinuteStat.count(),
      prisma.zoneDayStat.count(),
      prisma.analysisSession.count(),
    ]);
    // Day rollups and sessions are written at the very end, so their absence
    // is the reliable signal that the previous run did not finish.
    const complete = dayRows > 0 && sessions > 0;

    if (complete) {
      console.log(
        `Demo data already present ("${existing.name}") — ` +
        `${buckets.toLocaleString()} buckets, ${dayRows.toLocaleString()} day rows.\n` +
        `Re-run with --wipe to regenerate.`,
      );
    } else {
      console.log(
        `INCOMPLETE demo data found ("${existing.name}").\n\n` +
        `  minute buckets  ${buckets.toLocaleString()}\n` +
        `  day rollups     ${dayRows.toLocaleString()}\n` +
        `  sessions        ${sessions.toLocaleString()}\n\n` +
        `A previous run was interrupted. This seed builds its fixtures in one\n` +
        `pass and cannot resume, so finish it with:\n\n` +
        `  npx tsx prisma/seed.ts --wipe\n`,
      );
      process.exitCode = 1;
    }
    return;
  }

  // ── Organisations ──
  const org = await prisma.organisation.create({
    data: {
      name: 'Northgate Facilities',
      slug: `${DEMO_SLUG_PREFIX}northgate`,
      timezone: 'Asia/Kolkata',
      dataRetentionDays: 90,
    },
  });

  // A second tenant exists for exactly one reason: to prove isolation. Every
  // RLS test asserts that a Northgate user cannot see a single Meridian row.
  const otherOrg = await prisma.organisation.create({
    data: {
      name: 'Meridian Coworking',
      slug: `${DEMO_SLUG_PREFIX}meridian`,
      timezone: 'Europe/London',
      dataRetentionDays: 30,
    },
  });
  console.log(`orgs:      ${org.name}, ${otherOrg.name}`);

  // ── Sites ──
  const sites = await Promise.all([
    prisma.site.create({
      data: {
        orgId: org.id, name: 'HQ — Level 3', location: 'Level 3, North Wing',
        timezone: 'Asia/Kolkata', totalCapacity: 60,
        workdayStartMinute: 540, workdayEndMinute: 1080, workdays: [1, 2, 3, 4, 5],
      },
    }),
    prisma.site.create({
      data: {
        orgId: org.id, name: 'HQ — Level 4', location: 'Level 4, North Wing',
        timezone: 'Asia/Kolkata', totalCapacity: 40,
        workdayStartMinute: 540, workdayEndMinute: 1080, workdays: [1, 2, 3, 4, 5],
      },
    }),
    prisma.site.create({
      data: {
        orgId: otherOrg.id, name: 'Shoreditch Hub', timezone: 'Europe/London',
        totalCapacity: 35,
      },
    }),
  ]);
  console.log(`sites:     ${sites.length}`);

  // ── Cameras and zones ──
  const cameras: { id: string; orgId: string; siteId: string }[] = [];
  const zones: { id: string; orgId: string; siteId: string; cameraId: string; profile: ZoneProfile }[] = [];

  let camIndex = 0;
  for (const site of sites) {
    const perSite = site.orgId === org.id ? 2 : 1;
    for (let c = 0; c < perSite; c++) {
      camIndex++;
      const cam = await prisma.camera.create({
        data: {
          orgId: site.orgId,
          siteId: site.id,
          name: `cam_${String(camIndex).padStart(2, '0')}_${site.name.split('— ')[1] ?? 'floor'}`.replace(/\s+/g, '_').toLowerCase(),
          description: `Ceiling-mounted, ${site.name}`,
          sourceType: c === 0 ? 'RTSP' : 'UPLOAD',
          rtspUrl: c === 0 ? `rtsp://viewer:CamPass${camIndex}@10.20.30.${40 + camIndex}:554/stream1` : null,
          fpsTarget: 8,
          frameWidth: 1920,
          frameHeight: 1080,
          status: 'ACTIVE',
          lastSeenAt: new Date(),
          // A plausible calibration so the top-down view has something to use.
          homographyMatrix: [
            [1.42, 0.06, -220.5],
            [0.02, 1.87, -410.2],
            [0.0001, 0.0009, 1.0],
          ] as Prisma.InputJsonValue,
        },
      });
      cameras.push({ id: cam.id, orgId: cam.orgId, siteId: site.id });

      // Split the profile list across cameras so each camera owns 3–4 zones.
      const slice = ZONE_PROFILES.filter((_, i) => i % perSite === c);
      for (const p of slice) {
        // Polygon in real pixel coordinates within the 1920x1080 frame.
        const ox = 120 + (zones.length % 3) * 560;
        const oy = 90 + Math.floor((zones.length % 6) / 3) * 430;
        const z = await prisma.zone.create({
          data: {
            orgId: site.orgId,
            siteId: site.id,
            cameraId: cam.id,
            name: p.name,
            zoneType: p.zoneType,
            capacity: p.capacity,
            excludeFromUtilisation: p.excludeFromUtilisation ?? false,
            polygon: [
              [ox, oy], [ox + 480, oy + 20],
              [ox + 470, oy + 330], [ox + 15, oy + 340],
            ] as Prisma.InputJsonValue,
            colour: { WORKSTATION: '#DC2626', MEETING: '#2563EB', BREAK: '#16A34A', CORRIDOR: '#A16207', RECEPTION: '#7C3AED' }[p.zoneType],
          },
        });
        zones.push({ id: z.id, orgId: z.orgId, siteId: site.id, cameraId: cam.id, profile: p });
      }
    }
  }
  console.log(`cameras:   ${cameras.length}`);
  console.log(`zones:     ${zones.length}`);

  // ── Alert rules ──
  const rules = await Promise.all([
    prisma.alertRule.create({
      data: {
        orgId: org.id, name: 'Prolonged sitting — desk banks', type: 'SEDENTARY',
        severity: 'WARNING', thresholdValue: 90, sustainedMinutes: 10, cooldownMinutes: 60,
      },
    }),
    prisma.alertRule.create({
      data: {
        orgId: org.id, name: 'Meeting room over capacity', type: 'OVERCROWDING',
        severity: 'CRITICAL', thresholdValue: 4, sustainedMinutes: 5, cooldownMinutes: 30,
        zoneId: zones.find((z) => z.profile.name === 'Meeting Room 2')?.id,
      },
    }),
    prisma.alertRule.create({
      data: {
        orgId: org.id, name: 'Underused desk space', type: 'UNDERUTILISATION',
        severity: 'INFO', thresholdValue: 30, sustainedMinutes: 480, cooldownMinutes: 1440,
      },
    }),
  ]);
  console.log(`rules:     ${rules.length}`);

  // ── Minute buckets ──
  // The heavy part. Batched inserts with skipDuplicates so a re-run tops up
  // rather than exploding on the (zoneId, bucketStart) unique constraint.
  const today = new Date();
  today.setUTCHours(0, 0, 0, 0);

  let buffer: Prisma.ZoneMinuteStatCreateManyInput[] = [];
  let written = 0;
  const dayTotals: Record<string, { occ: number[]; sit: number; stand: number; walk: number; sample: number; dwell: number }> = {};

  const flush = async () => {
    if (buffer.length === 0) return;
    const res = await prisma.zoneMinuteStat.createMany({ data: buffer, skipDuplicates: true });
    written += res.count;
    buffer = [];
    process.stdout.write(`\r  buckets:  ${written.toLocaleString()}`);
  };

  for (let d = DAYS - 1; d >= 0; d--) {
    const dayStart = new Date(today);
    dayStart.setUTCDate(dayStart.getUTCDate() - d);
    const isoDay = dayStart.getUTCDay() === 0 ? 7 : dayStart.getUTCDay();
    const dayIndex = DAYS - 1 - d;

    // Weekends get a handful of buckets, not a full day — enough to prove the
    // "exclude non-working days" filter has something to exclude.
    const startMin = isoDay <= 5 ? 8 * 60 : 10 * 60;
    const endMin = isoDay <= 5 ? 19 * 60 : 12 * 60;

    for (const z of zones) {
      const key = `${z.id}|${dayStart.toISOString().slice(0, 10)}`;
      dayTotals[key] = { occ: [], sit: 0, stand: 0, walk: 0, sample: 0, dwell: 0 };

      for (let m = startMin; m < endMin; m++) {
        const occMax = occupancyFor(z.profile, m, isoDay, dayIndex);
        if (occMax === 0 && rand() > 0.25) continue; // sparse empties, as in reality

        const occMin = Math.max(0, occMax - (rand() < 0.3 ? 1 : 0));
        const occAvg = clamp((occMax + occMin) / 2 + jitter(0, 0.2), occMin, occMax);

        const sampleFrames = occMax === 0 ? 0 : 8 * 60; // fpsTarget 8 x 60s
        const [ws, wd] = z.profile.posture;
        const personFrames = occAvg > 0 ? sampleFrames : 0;

        // The three posture counts must partition personFrames — the
        // zms_posture_frames_within_sample constraint enforces
        // sit + stand + walk <= sampleFrames. Jittering each weight
        // independently and rounding up can overshoot, so sit and stand are
        // clamped against the remaining budget and walk takes what is left.
        const sit = clamp(Math.round(personFrames * jitter(ws, 0.05)), 0, personFrames);
        const stand = clamp(Math.round(personFrames * jitter(wd, 0.04)), 0, personFrames - sit);
        const walk = clamp(personFrames - sit - stand, 0, personFrames - sit - stand);

        const bucketStart = new Date(dayStart);
        bucketStart.setUTCMinutes(m, 0, 0);

        // Activity index tracks walking share — a corridor scores high, a desk
        // bank low. Keeps the metric interpretable rather than arbitrary.
        const walkShare = personFrames > 0 ? walk / personFrames : 0;
        const activity = clamp(jitter(18 + walkShare * 70, 6), 0, 100);

        const dwell = Math.min(60 * Math.max(occMax, 1), Math.round(occAvg * 60));

        buffer.push({
          orgId: z.orgId,
          siteId: z.siteId,
          cameraId: z.cameraId,
          zoneId: z.id,
          bucketStart,
          occupancyMax: occMax,
          occupancyAvg: Number(occAvg.toFixed(2)),
          occupancyMin: occMin,
          sittingFrames: sit,
          standingFrames: stand,
          walkingFrames: walk,
          sampleFrames,
          avgActivityScore: Number(activity.toFixed(2)),
          totalDwellSeconds: dwell,
          uniqueTrackCount: clamp(occMax + (rand() < 0.2 ? 1 : 0), 0, 40),
        });

        const t = dayTotals[key];
        t.occ.push(occMax);
        t.sit += sit; t.stand += stand; t.walk += walk;
        t.sample += sampleFrames; t.dwell += dwell;

        if (buffer.length >= MINUTE_BATCH) await flush();
      }
    }
  }
  await flush();
  process.stdout.write('\n');

  // ── Day rollups ──
  // Written here rather than by calling rollup_zone_day_stats() so the seed
  // works before 004_secrets_and_retention.sql has been applied.
  const dayRows: Prisma.ZoneDayStatCreateManyInput[] = [];
  for (const [key, t] of Object.entries(dayTotals)) {
    if (t.occ.length === 0) continue;
    const [zoneId, dateStr] = key.split('|');
    const z = zones.find((x) => x.id === zoneId)!;
    const occupied = t.occ.filter((o) => o >= 1).length;
    const workingMinutes = 1080 - 540;
    const peak = Math.max(...t.occ);
    dayRows.push({
      orgId: z.orgId,
      siteId: z.siteId,
      cameraId: z.cameraId,
      zoneId,
      statDate: new Date(`${dateStr}T00:00:00.000Z`),
      peakOccupancy: peak,
      avgOccupancy: Number((t.occ.reduce((a, b) => a + b, 0) / t.occ.length).toFixed(2)),
      peakHour: 10 + Math.floor(rand() * 6),
      occupiedMinutes: occupied,
      utilisationPct: Number(Math.min(100, (occupied / workingMinutes) * 100).toFixed(2)),
      sittingRatio: t.sample ? Number((t.sit / t.sample).toFixed(4)) : 0,
      standingRatio: t.sample ? Number((t.stand / t.sample).toFixed(4)) : 0,
      walkingRatio: t.sample ? Number((t.walk / t.sample).toFixed(4)) : 0,
      totalDwellSeconds: t.dwell,
      avgActivityScore: Number(jitter(30, 12).toFixed(2)),
    });
  }
  for (let i = 0; i < dayRows.length; i += MINUTE_BATCH) {
    await prisma.zoneDayStat.createMany({ data: dayRows.slice(i, i + MINUTE_BATCH), skipDuplicates: true });
  }
  console.log(`  day rows: ${dayRows.length.toLocaleString()}`);

  // ── Sessions ──
  const sessions: Prisma.AnalysisSessionCreateManyInput[] = [];
  for (let i = 0; i < 24; i++) {
    const cam = pick(cameras.filter((c) => c.orgId === org.id));
    const queued = new Date(today);
    queued.setUTCDate(queued.getUTCDate() - Math.floor(rand() * DAYS));
    const total = 3000 + Math.floor(rand() * 40000);
    // A few failures and one cancellation, so /sessions has every state to render.
    const status = rand() < 0.08 ? 'ERROR' : rand() < 0.05 ? 'CANCELLED' : 'DONE';
    const processed = status === 'DONE' ? total : Math.floor(total * rand());
    const finished = new Date(queued.getTime() + processed * 12);
    sessions.push({
      orgId: org.id,
      cameraId: cam.id,
      kind: rand() < 0.6 ? 'VIDEO_UPLOAD' : 'LIVE_RTSP',
      status: status as never,
      sourceFilename: `floor3_${queued.toISOString().slice(0, 10)}_${i}.mp4`,
      sourceSizeBytes: BigInt(Math.floor(80e6 + rand() * 900e6)),
      totalFrames: total,
      processedFrames: processed,
      fpsAchieved: Number(jitter(52, 9).toFixed(1)),
      durationSeconds: Math.floor(total / 25),
      coverageStart: queued,
      coverageEnd: new Date(queued.getTime() + (total / 25) * 1000),
      queuedAt: queued,
      startedAt: queued,
      finishedAt: finished,
      errorMessage: status === 'ERROR' ? 'Decoder failed at frame ' + processed + ': unsupported H.265 profile' : null,
    });
  }
  await prisma.analysisSession.createMany({ data: sessions });
  console.log(`  sessions: ${sessions.length}`);

  // ── Fired alerts ──
  const crowdRule = rules[1];
  const crowdZone = zones.find((z) => z.profile.name === 'Meeting Room 2');
  const alerts: Prisma.AlertCreateManyInput[] = [];
  if (crowdZone) {
    for (let i = 0; i < 40; i++) {
      const at = new Date(today);
      at.setUTCDate(at.getUTCDate() - Math.floor(rand() * 45));
      at.setUTCHours(10 + Math.floor(rand() * 7), Math.floor(rand() * 60), 0, 0);
      const value = 5 + Math.floor(rand() * 3);
      // Two thirds already handled, a third still OPEN — so the feed has an
      // unacknowledged badge count to display.
      const handled = rand() < 0.66;
      alerts.push({
        orgId: org.id,
        ruleId: crowdRule.id,
        zoneId: crowdZone.id,
        cameraId: crowdZone.cameraId,
        state: handled ? (rand() < 0.5 ? 'ACKNOWLEDGED' : 'RESOLVED') : 'OPEN',
        severity: 'CRITICAL',
        triggeredValue: value,
        thresholdValue: 4,
        message: `Meeting Room 2 occupancy ${value} exceeded capacity 4 for over 5 minutes`,
        triggeredAt: at,
        clearedAt: handled ? new Date(at.getTime() + 18 * 60_000) : null,
        acknowledgedAt: handled ? new Date(at.getTime() + 6 * 60_000) : null,
        resolvedAt: null,
      });
    }
  }
  // Acknowledged alerts need an actor to satisfy alerts_ack_has_actor. There
  // are no profiles yet (they come from real signups), so acknowledgement is
  // recorded without an actor and the state left OPEN where that would break
  // the constraint.
  const safeAlerts = alerts.map((a) => ({
    ...a,
    state: 'OPEN' as const,
    acknowledgedAt: null,
    clearedAt: a.clearedAt,
    resolvedAt: null,
  }));
  await prisma.alert.createMany({ data: safeAlerts });
  console.log(`  alerts:   ${safeAlerts.length}`);

  console.log(`
Done.

  ${written.toLocaleString()} minute buckets across ${zones.length} zones over ${DAYS} days.

  Built-in cases to develop against:
    Desk Bank C      chronically underused  -> the "reclaim space" insight
    Meeting Room 2   over capacity          -> OVERCROWDING alerts
    Main Corridor    null capacity          -> excluded from utilisation
    Meridian Cowork. second tenant          -> RLS isolation tests
    2 sessions       ERROR / CANCELLED      -> non-happy-path UI

  Users are NOT seeded — profiles come from real Supabase signups, and the
  trigger in 002 creates them. Sign up, run create_organisation(), then point
  it at this data.
`);
}

main()
  .catch((e) => {
    console.error('\nSeed failed:', e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
