// frontend/scripts/test-plan-terms.mjs
//
// Checks plan terms, expiry, renewal, limits, the billing guard and scheduled reports against the real database
// (029_plan_term_enforcement.sql). Everything runs inside ONE transaction that is rolled back at the end, so it
// leaves no rows behind.
//
//   node scripts/test-plan-terms.mjs
import fs from 'node:fs';
import crypto from 'node:crypto';
import pg from 'pg';

const env = Object.fromEntries(
  fs.readFileSync(new URL('../.env.local', import.meta.url), 'utf8').split(/\r?\n/)
    .filter((l) => /^[A-Z_]+=/.test(l)).map((l) => { const i = l.indexOf('='); return [l.slice(0, i), l.slice(i + 1).replace(/^"|"$/g, '')]; }));
const db = new pg.Client({ connectionString: env.DIRECT_URL || env.DATABASE_URL, ssl: { rejectUnauthorized: false } });

let passed = 0, failed = 0;
const check = (name, ok, detail = '') => {
  if (ok) { passed += 1; console.log(`  ok    ${name}`); }
  else { failed += 1; console.log(`  FAIL  ${name}${detail ? `  (${detail})` : ''}`); }
};
const q = async (sql, params) => (await db.query(sql, params)).rows;
// Run as the signed-in user (RLS and the guard apply), then back to the owner role.
const asUser = async (uid, fn) => {
  await db.query('SAVEPOINT u');
  await db.query(`SELECT set_config('request.jwt.claims', $1, true)`, [JSON.stringify({ sub: uid, role: 'authenticated' })]);
  await db.query('SET LOCAL ROLE authenticated');
  try { const r = await fn(); await db.query('RESET ROLE'); await db.query('RELEASE SAVEPOINT u'); return { ok: true, r }; }
  catch (e) { await db.query('ROLLBACK TO SAVEPOINT u'); await db.query('RESET ROLE'); return { ok: false, error: e.message }; }
};
// An owner-role statement that is expected to fail, without aborting the transaction.
const attempt = async (sql, params) => {
  await db.query('SAVEPOINT a');
  try { await db.query(sql, params); await db.query('RELEASE SAVEPOINT a'); return { ok: true }; }
  catch (e) { await db.query('ROLLBACK TO SAVEPOINT a'); return { ok: false, error: e.message }; }
};
const days = (a, b) => Math.round((new Date(b) - new Date(a)) / 86400000);

await db.connect();
await db.query('BEGIN');
try {
  const uid = crypto.randomUUID();
  const org = crypto.randomUUID();
  await q(`INSERT INTO auth.users (id, instance_id, aud, role, email, encrypted_password, email_confirmed_at, created_at, updated_at, raw_user_meta_data, raw_app_meta_data)
           VALUES ($1, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', $2, '', now(), now(), now(), '{"full_name":"Plan Test"}', '{}')`,
          [uid, `plan.test.${uid.slice(0, 8)}@example.com`]);
  await q(`INSERT INTO organisations (id, name, slug, plan, "billingPeriod", "planStartedAt", "planRenewsAt", "updatedAt")
           VALUES ($1, 'Plan Test Org', $2, 'GROWTH', 'MONTHLY', now(), now() + interval '1 month', now())`, [org, `plan-test-${org.slice(0, 8)}`]);
  await q(`INSERT INTO memberships ("orgId", "profileId", role, status, "invitedEmail", "acceptedAt", "updatedAt")
           VALUES ($1, $2, 'ADMIN', 'ACTIVE', 'admin@example.com', now(), now())`, [org, uid]);
  await q(`UPDATE profiles SET "currentOrgId" = $1 WHERE id = $2`, [org, uid]);

  const usage = async () => (await asUser(uid, () => q('SELECT * FROM plan_usage($1)', [org]))).r[0];
  const addCamera = (n) => attempt(`INSERT INTO cameras ("orgId", name, "sourceType", "updatedAt") VALUES ($1, $2, 'UPLOAD', now())`, [org, `cam-${n}`]);
  const addSite = (n) => attempt(`INSERT INTO sites ("orgId", name, "updatedAt") VALUES ($1, $2, now())`, [org, `site-${n}`]);
  const addSeat = (n, status = 'INVITED') => attempt(`INSERT INTO memberships ("orgId", role, status, "invitedEmail", "updatedAt") VALUES ($1, 'VIEWER', $3, $2, now())`, [org, `m${n}@example.com`, status]);
  const expire = () => q(`UPDATE organisations SET "planRenewsAt" = now() - interval '1 minute' WHERE id = $1`, [org]);

  console.log('\nTerms');
  let r = await asUser(uid, () => q(`SELECT * FROM change_plan('GROWTH', 'MONTHLY')`));
  let u = await usage();
  check('monthly Growth: term is one month', r.r?.[0]?.ok && [28, 29, 30, 31].includes(days(u.started_at, u.renews_at)), `${days(u.started_at, u.renews_at)} days`);
  r = await asUser(uid, () => q(`SELECT * FROM change_plan('GROWTH', 'YEARLY')`));
  u = await usage();
  check('yearly Growth: term is one year', r.r?.[0]?.ok && [365, 366].includes(days(u.started_at, u.renews_at)), `${days(u.started_at, u.renews_at)} days`);
  check('yearly Growth: billed yearly', u.billing_period === 'YEARLY');
  r = await asUser(uid, () => q(`SELECT * FROM change_plan('FREE', 'MONTHLY')`));
  u = await usage();
  check('Starter: does not expire', r.r?.[0]?.ok && u.renews_at === null);
  await asUser(uid, () => q(`SELECT * FROM change_plan('GROWTH', 'MONTHLY')`));

  console.log('\nGrowth limits (10 cameras, 5 sites, 25 members, 90 days)');
  let okCams = 0; for (let i = 1; i <= 10; i += 1) if ((await addCamera(i)).ok) okCams += 1;
  check('10 cameras can be added', okCams === 10, `${okCams}`);
  r = await addCamera(11);
  check('the 11th camera is refused', !r.ok && /plan_limit_cameras/.test(r.error), r.error);
  let okSites = 0; for (let i = 1; i <= 5; i += 1) if ((await addSite(i)).ok) okSites += 1;
  check('5 sites can be added', okSites === 5, `${okSites}`);
  r = await addSite(6);
  check('the 6th site is refused', !r.ok && /plan_limit_sites/.test(r.error), r.error);
  let okSeats = 0; for (let i = 2; i <= 25; i += 1) if ((await addSeat(i)).ok) okSeats += 1;   // + the admin = 25
  check('25 members (admin + 24 invitations) fit', okSeats === 24, `${okSeats}`);
  r = await addSeat(26);
  check('the 26th member is refused', !r.ok && /plan_limit_seats/.test(r.error), r.error);
  u = await usage();
  check('usage counts 10 / 5 / 25', u.cameras_used === 10 && u.sites_used === 5 && u.seats_used === 25, JSON.stringify([u.cameras_used, u.sites_used, u.seats_used]));
  check('history limit is 90 days', u.retention_max === 90);

  console.log('\nRestoring a member counts against the limit');
  await q(`UPDATE memberships SET status = 'SUSPENDED' WHERE "orgId" = $1 AND "invitedEmail" = 'm2@example.com'`, [org]);
  check('suspending frees a seat', (await addSeat(30)).ok);
  r = await attempt(`UPDATE memberships SET status = 'ACTIVE' WHERE "orgId" = $1 AND "invitedEmail" = 'm2@example.com'`, [org]);
  check('restoring when full is refused', !r.ok && /plan_limit_seats/.test(r.error), r.error);

  console.log('\nScheduled reports');
  r = await asUser(uid, () => q(`SELECT * FROM set_report_schedule('WEEKLY')`));
  check('Growth can schedule a weekly report', r.r?.[0]?.ok, r.error || r.r?.[0]?.message);
  await q(`UPDATE organisations SET "reportScheduleNextAt" = now() - interval '1 minute' WHERE id = $1`, [org]);
  let made = (await q('SELECT run_scheduled_reports() n'))[0].n;
  const rep = await q(`SELECT title, "rangeEnd" - "rangeStart" AS span FROM reports WHERE "orgId" = $1`, [org]);
  check('a due weekly report is made', made === 1 && rep.length === 1 && rep[0].span.days === 7, JSON.stringify(rep));
  const next = (await q(`SELECT "reportScheduleNextAt" n FROM organisations WHERE id = $1`, [org]))[0].n;
  check('the next one is due in the future', new Date(next) > new Date());

  console.log('\nThe billing guard');
  r = await asUser(uid, () => q(`UPDATE organisations SET "planRenewsAt" = now() + interval '5 years' WHERE id = $1`, [org]));
  check('an admin cannot extend the term directly', !r.ok && /billing_columns/.test(r.error), r.error);
  r = await asUser(uid, () => q(`UPDATE organisations SET plan = 'ENTERPRISE' WHERE id = $1`, [org]));
  check('an admin cannot change the tier directly', !r.ok && /billing_columns/.test(r.error), r.error);
  r = await asUser(uid, () => q(`UPDATE organisations SET name = 'Renamed Org' WHERE id = $1 RETURNING name`, [org]));
  check('other organisation fields still save', r.ok && r.r?.[0]?.name === 'Renamed Org', r.error);

  console.log('\nRenewal');
  r = await asUser(uid, () => q(`SELECT * FROM renew_plan()`));
  check('renewing with weeks left is refused', r.r?.[0]?.ok === false && /last 7 days/.test(r.r[0].message), r.r?.[0]?.message);
  await q(`UPDATE organisations SET "planRenewsAt" = now() + interval '3 days' WHERE id = $1`, [org]);
  const before = (await usage()).renews_at;
  r = await asUser(uid, () => q(`SELECT * FROM renew_plan()`));
  u = await usage();
  check('renewing in the last 7 days extends from the end date', r.r?.[0]?.ok && [28, 29, 30, 31].includes(days(before, u.renews_at)), `${days(before, u.renews_at)} days, ${r.r?.[0]?.message}`);

  console.log('\nExpiry');
  await expire();
  u = await usage();
  check('an ended term is reported as expired', u.expired === true && u.plan === 'GROWTH' && u.effective_plan === 'FREE');
  check('Starter limits apply: 1 / 1 / 3 / 7', u.cameras_max === 1 && u.sites_max === 1 && u.seats_max === 3 && u.retention_max === 7);
  check('history falls to 7 days', u.retention_days === 7, `${u.retention_days}`);
  r = await addCamera(12);
  check('no camera can be added after expiry', !r.ok && /the FREE plan allows 1/.test(r.error), r.error);
  check('no site can be added after expiry', !(await addSite(7)).ok);
  check('no member can be invited after expiry', !(await addSeat(40)).ok);
  check('existing cameras are kept', (await q(`SELECT count(*)::int n FROM cameras WHERE "orgId" = $1`, [org]))[0].n === 10);
  r = await asUser(uid, () => q(`SELECT * FROM set_report_schedule('DAILY')`));
  check('reports cannot be scheduled after expiry', r.r?.[0]?.ok === false && /ended/.test(r.r[0].message), r.r?.[0]?.message);
  await q(`UPDATE organisations SET "reportScheduleNextAt" = now() - interval '1 minute' WHERE id = $1`, [org]);
  made = (await q('SELECT run_scheduled_reports() n'))[0].n;
  check('scheduled reports pause after expiry', made === 0);

  console.log('\nRenewing after expiry');
  r = await asUser(uid, () => q(`SELECT * FROM renew_plan('YEARLY')`));
  u = await usage();
  check('renewing an ended plan starts a new term from today', r.r?.[0]?.ok && u.expired === false && [365, 366].includes(days(new Date(), u.renews_at)), r.r?.[0]?.message);
  check('Growth limits are back', u.cameras_max === 10 && u.retention_max === 90);
  await q(`DELETE FROM cameras WHERE "orgId" = $1 AND name = 'cam-1'`, [org]);
  check('cameras can be added again (up to 10)', (await addCamera(13)).ok);

  console.log('\nEnterprise (unlimited)');
  await asUser(uid, () => q(`SELECT * FROM change_plan('ENTERPRISE', 'MONTHLY')`));
  let extra = 0; for (let i = 100; i < 115; i += 1) if ((await addCamera(i)).ok) extra += 1;
  check('Enterprise has no camera ceiling', extra === 15, `${extra}`);
  u = await usage();
  check('Enterprise history is 365 days', u.retention_max === 365);

  console.log('\nHistory follows the plan (030)');
  const retention = async () => (await q(`SELECT "dataRetentionDays" d FROM organisations WHERE id = $1`, [org]))[0].d;
  await q(`UPDATE organisations SET plan = 'FREE', "planRenewsAt" = NULL, "dataRetentionDays" = 7 WHERE id = $1`, [org]);
  await q(`UPDATE organisations SET plan = 'GROWTH', "planRenewsAt" = now() + interval '1 month' WHERE id = $1`, [org]);
  check('Starter (7 days) upgraded to Growth keeps 90 days', (await retention()) === 90, `${await retention()}`);
  await q(`UPDATE organisations SET "dataRetentionDays" = 30 WHERE id = $1`, [org]);
  await q(`UPDATE organisations SET plan = 'ENTERPRISE' WHERE id = $1`, [org]);
  check('a shorter history someone chose is kept on upgrade', (await retention()) === 30, `${await retention()}`);
  await q(`UPDATE organisations SET plan = 'GROWTH', "dataRetentionDays" = 90, "planRenewsAt" = now() - interval '1 minute' WHERE id = $1`, [org]);
  await q(`UPDATE organisations SET "dataRetentionDays" = 7 WHERE id = $1`, [org]);
  r = await asUser(uid, () => q(`SELECT * FROM renew_plan()`));
  check('renewing an expired plan brings 90 days back', r.r?.[0]?.ok && (await retention()) === 90, `${await retention()} ${r.r?.[0]?.message}`);
  await q(`UPDATE organisations SET plan = 'ENTERPRISE' WHERE id = $1`, [org]);            // room for the manager below

  console.log('\nOnly an administrator renews, and only a paid plan');
  const mgr = crypto.randomUUID();
  await q(`INSERT INTO auth.users (id, instance_id, aud, role, email, encrypted_password, email_confirmed_at, created_at, updated_at, raw_user_meta_data, raw_app_meta_data)
           VALUES ($1, '00000000-0000-0000-0000-000000000000', 'authenticated', 'authenticated', $2, '', now(), now(), now(), '{}', '{}')`,
          [mgr, `plan.mgr.${mgr.slice(0, 8)}@example.com`]);
  await q(`INSERT INTO memberships ("orgId", "profileId", role, status, "invitedEmail", "acceptedAt", "updatedAt")
           VALUES ($1, $2, 'MANAGER', 'ACTIVE', 'mgr@example.com', now(), now())`, [org, mgr]);
  await q(`UPDATE profiles SET "currentOrgId" = $1 WHERE id = $2`, [org, mgr]);
  await expire();
  r = await asUser(mgr, () => q(`SELECT * FROM renew_plan()`));
  check('a manager cannot renew', r.r?.[0]?.ok === false && /administrator/.test(r.r[0].message), r.r?.[0]?.message);
  r = await asUser(mgr, () => q(`SELECT * FROM change_plan('GROWTH', 'MONTHLY')`));
  check('a manager cannot change the plan', r.r?.[0]?.ok === false, r.r?.[0]?.message);
  await q(`UPDATE organisations SET plan = 'FREE', "planRenewsAt" = NULL WHERE id = $1`, [org]);
  r = await asUser(uid, () => q(`SELECT * FROM renew_plan()`));
  check('the Starter plan cannot be renewed', r.r?.[0]?.ok === false && /free/.test(r.r[0].message), r.r?.[0]?.message);
} catch (e) {
  failed += 1;
  console.error('ERROR', e.message);
} finally {
  await db.query('ROLLBACK');
  await db.end();
}
console.log(`\n${passed} passed, ${failed} failed (everything rolled back)`);
process.exitCode = failed ? 1 : 0;
