// frontend/scripts/check-plan-limits.mjs
//
// Fails if app/lib/plans.js and public.plan_limits disagree.
//
// WHY THIS SCRIPT EXISTS
//
// The limits are necessarily written twice: `plans.js` RENDERS them on the
// pricing cards, and Postgres ENFORCES them in a trigger. Neither can read the
// other — a browser cannot import a Postgres function, and a trigger cannot
// import a JS module.
//
// Duplication that nothing checks is duplication that drifts, and the failure
// mode here is the worst kind: the pricing page would advertise 10 cameras
// while the database refused the second one, and nothing would break until a
// user hit it. So the duplication stays, and this makes it CHECKABLE.
//
// The DATABASE IS THE AUTHORITY. If the two disagree, the SQL table is right
// by definition — it is the thing that actually refuses the insert. This script
// reports `plans.js` as the file to fix.
//
//   node scripts/check-plan-limits.mjs
//
// Exit 0 = they agree. Exit 1 = drift (or the DB is unreachable).

import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve } from 'node:path';
import pg from 'pg';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');

const env = {};
try {
  for (const line of readFileSync(resolve(root, '.env.local'), 'utf8').split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$/);
    if (m) env[m[1]] = m[2].trim().replace(/^["']|["']$/g, '');
  }
} catch {
  console.error('check-plan-limits: cannot read frontend/.env.local');
  process.exit(1);
}

const url = env.DIRECT_URL || env.DATABASE_URL;
if (!url) {
  console.error('check-plan-limits: no DIRECT_URL or DATABASE_URL in .env.local');
  process.exit(1);
}

// pathToFileURL, not the bare path: on Windows an absolute path starts with a
// drive letter, which Node's ESM loader reads as an unsupported URL scheme
// ("protocol 'd:'"). This is the portable form.
const { PLANS } = await import(pathToFileURL(resolve(root, 'app/lib/plans.js')).href);

const client = new pg.Client({ connectionString: url, ssl: { rejectUnauthorized: false } });
await client.connect();

let rows;
try {
  rows = (await client.query('SELECT * FROM public.plan_limits')).rows;
} catch (e) {
  console.error('check-plan-limits: cannot read public.plan_limits —', e.message);
  console.error('  Has prisma/sql/015_plan_limits.sql been applied?');
  await client.end();
  process.exit(1);
}
await client.end();

const db = new Map(rows.map((r) => [r.plan, r]));
const problems = [];

// `plans.js` uses null for "unlimited"; SQL uses NULL. Both normalise to null
// here so the comparison never mistakes one for a limit of zero.
const norm = (v) => (v === null || v === undefined ? null : Number(v));

for (const plan of PLANS) {
  const row = db.get(plan.id);
  if (!row) {
    problems.push(`${plan.id}: present in plans.js but MISSING from public.plan_limits`);
    continue;
  }
  const pairs = [
    ['cameras', plan.limits.cameras, row.max_cameras],
    ['sites', plan.limits.sites, row.max_sites],
    ['seats', plan.limits.seats, row.max_seats],
    ['retentionDays', plan.limits.retentionDays, row.max_retention_days],
  ];
  for (const [name, js, sql] of pairs) {
    if (norm(js) !== norm(sql)) {
      problems.push(
        `${plan.id}.${name}: plans.js says ${js === null ? 'unlimited' : js}, ` +
        `database says ${sql === null ? 'unlimited' : sql}`,
      );
    }
  }
}

for (const [id] of db) {
  if (!PLANS.some((p) => p.id === id)) {
    problems.push(`${id}: present in public.plan_limits but MISSING from plans.js`);
  }
}

if (problems.length) {
  console.error('\nPLAN LIMITS HAVE DRIFTED\n');
  for (const p of problems) console.error('  ' + p);
  console.error('\nThe DATABASE is the authority — it is what refuses the write.');
  console.error('Fix app/lib/plans.js to match, or write a migration to change the table.\n');
  process.exit(1);
}

console.log(`plan limits agree — ${PLANS.length} tiers x 4 limits verified against public.plan_limits`);
