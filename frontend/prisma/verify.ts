/**
 * Post-deployment verification.
 *
 * Confirms that everything the four SQL files were supposed to create actually
 * exists in the live database. "Script executed successfully" only means the
 * statements parsed and ran — it does not prove the objects are there, or that
 * a REVOKE was not silently overridden later.
 *
 *   npx tsx prisma/verify.ts
 *
 * Read-only. Safe against production.
 */

import { config as loadEnv } from 'dotenv';
loadEnv({ path: '.env.local' });
loadEnv();

import { PrismaPg } from '@prisma/adapter-pg';
import { PrismaClient } from '../app/generated/prisma/client';

const connectionString = process.env.DIRECT_URL ?? process.env.DATABASE_URL;
if (!connectionString) {
  console.error('Set DIRECT_URL or DATABASE_URL in .env.local');
  process.exit(1);
}

const prisma = new PrismaClient({ adapter: new PrismaPg({ connectionString }) });

let pass = 0;
let fail = 0;

function check(label: string, ok: boolean, detail = '') {
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${label}${detail ? ` — ${detail}` : ''}`);
  ok ? pass++ : fail++;
}

async function one<T>(sql: string): Promise<T> {
  const r = await prisma.$queryRawUnsafe<Record<string, T>[]>(sql);
  return Object.values(r[0])[0];
}

async function main() {
  console.log('\nVisionWorks — live database verification\n');

  // ── Tables ──
  console.log('Schema');
  const tables = Number(
    await one<bigint>(`SELECT count(*)::bigint FROM information_schema.tables
      WHERE table_schema='public' AND table_type='BASE TABLE'
        AND table_name <> '_prisma_migrations'`),
  );
  // 13 tenant tables + platform_admins + platform_audit_logs from 005.
  check('15 tables created', tables === 15, `${tables} found`);

  const enums = Number(
    await one<bigint>(`SELECT count(*)::bigint FROM pg_type t
      JOIN pg_namespace n ON n.oid=t.typnamespace
      WHERE n.nspname='public' AND t.typtype='e'`),
  );
  check('15 enums created', enums === 15, `${enums} found`);

  const indexes = Number(
    await one<bigint>(`SELECT count(*)::bigint FROM pg_indexes WHERE schemaname='public'`),
  );
  check('indexes created', indexes >= 52, `${indexes} found`);

  // ── 001: constraints ──
  console.log('\nConstraints (001)');
  const checks = Number(
    await one<bigint>(`SELECT count(*)::bigint FROM pg_constraint c
      JOIN pg_namespace n ON n.oid=c.connamespace
      WHERE n.nspname='public' AND c.contype='c'
        AND c.conname NOT LIKE '%_not_null'`),
  );
  check('CHECK constraints present', checks >= 25, `${checks} found`);

  const adminTrig = Number(
    await one<bigint>(`SELECT count(*)::bigint FROM pg_trigger
      WHERE tgname='memberships_keep_an_admin' AND NOT tgisinternal`),
  );
  check('last-admin trigger', adminTrig === 1);

  // ── 002: auth bridge ──
  console.log('\nAuth bridge (002)');
  for (const t of ['on_auth_user_created', 'on_auth_user_email_changed']) {
    const n = Number(
      await one<bigint>(`SELECT count(*)::bigint FROM pg_trigger
        WHERE tgname='${t}' AND NOT tgisinternal`),
    );
    check(`trigger ${t}`, n === 1);
  }

  for (const f of [
    'handle_new_auth_user',
    'create_organisation',
    'user_org_ids',
    'admin_org_ids',
    'manage_org_ids',
    'user_has_role',
  ]) {
    const n = Number(
      await one<bigint>(`SELECT count(*)::bigint FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='${f}'`),
    );
    check(`function ${f}()`, n >= 1);
  }

  // ── 003: RLS ──
  console.log('\nRow Level Security (003)');
  const noRls = await prisma.$queryRawUnsafe<{ tablename: string }[]>(
    `SELECT tablename FROM pg_tables
     WHERE schemaname='public' AND NOT rowsecurity
       AND tablename <> '_prisma_migrations'`,
  );
  check('RLS enabled on every table', noRls.length === 0,
    noRls.length ? `missing on: ${noRls.map((r) => r.tablename).join(', ')}` : '');

  const policies = Number(
    await one<bigint>(`SELECT count(*)::bigint FROM pg_policies WHERE schemaname='public'`),
  );
  check('policies created', policies >= 30, `${policies} found`);

  // The old schema's permissive policies must be gone.
  const permissive = await prisma.$queryRawUnsafe<{ tablename: string; policyname: string }[]>(
    `SELECT tablename, policyname FROM pg_policies
     WHERE schemaname='public' AND (qual = 'true' OR with_check = 'true')`,
  );
  check('no USING(true) policies remain', permissive.length === 0,
    permissive.length ? permissive.map((p) => `${p.tablename}.${p.policyname}`).join(', ') : '');

  // Every policy must use the fast IN-list form, not a boolean helper.
  const slowPolicies = await prisma.$queryRawUnsafe<{ tablename: string; policyname: string }[]>(
    `SELECT tablename, policyname FROM pg_policies
     WHERE schemaname='public'
       AND (COALESCE(qual,'') || COALESCE(with_check,'')) ~ 'can_read_org|can_manage_org|is_org_admin'`,
  );
  check('no per-row boolean helpers in policies', slowPolicies.length === 0,
    slowPolicies.length ? slowPolicies.map((p) => `${p.tablename}.${p.policyname}`).join(', ') : '');

  // anon must have no table access at all.
  const anonGrants = Number(
    await one<bigint>(`SELECT count(*)::bigint FROM information_schema.role_table_grants
      WHERE table_schema='public' AND grantee='anon'`),
  );
  check('anon has no table grants', anonGrants === 0, `${anonGrants} grants`);

  // Analytics must be read-only to browser clients.
  for (const t of ['zone_minute_stats', 'zone_day_stats']) {
    const w = Number(
      await one<bigint>(`SELECT count(*)::bigint FROM information_schema.role_table_grants
        WHERE table_schema='public' AND table_name='${t}' AND grantee='authenticated'
          AND privilege_type IN ('INSERT','UPDATE','DELETE')`),
    );
    check(`${t} read-only to clients`, w === 0, `${w} write grants`);
  }

  const auditMut = Number(
    await one<bigint>(`SELECT count(*)::bigint FROM information_schema.role_table_grants
      WHERE table_schema='public' AND table_name='audit_logs' AND grantee='authenticated'
        AND privilege_type IN ('UPDATE','DELETE')`),
  );
  check('audit_logs append-only', auditMut === 0, `${auditMut} mutate grants`);

  // ── 004: credentials + jobs ──
  console.log('\nCredentials & retention (004)');

  // THE regression that was caught in testing: a table-level GRANT SELECT
  // silently re-exposes the column even after a column-level REVOKE.
  const rtspReadable = Number(
    await one<bigint>(`SELECT count(*)::bigint FROM information_schema.column_privileges
      WHERE table_schema='public' AND table_name='cameras'
        AND column_name='rtspUrl' AND grantee='authenticated'
        AND privilege_type='SELECT'`),
  );
  check('rtspUrl NOT readable by clients', rtspReadable === 0,
    rtspReadable ? 'CREDENTIAL EXPOSED' : '');

  const rtspWritable = Number(
    await one<bigint>(`SELECT count(*)::bigint FROM information_schema.column_privileges
      WHERE table_schema='public' AND table_name='cameras'
        AND column_name='rtspUrl' AND grantee='authenticated'
        AND privilege_type IN ('INSERT','UPDATE')`),
  );
  check('rtspUrl still writable (managers can set it)', rtspWritable >= 1);

  const safeView = Number(
    await one<bigint>(`SELECT count(*)::bigint FROM information_schema.views
      WHERE table_schema='public' AND table_name='cameras_safe'`),
  );
  check('cameras_safe view exists', safeView === 1);

  const redacted = await one<string>(
    `SELECT public.redact_rtsp_url('rtsp://admin:S3cret@10.0.0.5:554/s1')`,
  );
  check('redact_rtsp_url strips credentials',
    redacted === 'rtsp://****@10.0.0.5:554/s1', redacted);

  for (const f of ['rollup_zone_day_stats', 'purge_expired_minute_stats', 'expire_stale_reports']) {
    const n = Number(
      await one<bigint>(`SELECT count(*)::bigint FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='${f}'`),
    );
    check(`function ${f}()`, n === 1);
  }

  // ── 005: platform operator ──
  console.log('\nPlatform operator (005)');

  const paTables = Number(
    await one<bigint>(`SELECT count(*)::bigint FROM information_schema.tables
      WHERE table_schema='public'
        AND table_name IN ('platform_admins','platform_audit_logs')`),
  );
  check('platform tables created', paTables === 2, `${paTables}/2`);

  for (const f of ['is_platform_admin', 'grant_platform_admin', 'revoke_platform_admin']) {
    const n = Number(
      await one<bigint>(`SELECT count(*)::bigint FROM pg_proc p
        JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='${f}'`),
    );
    check(`function ${f}()`, n === 1);
  }

  const overview = Number(
    await one<bigint>(`SELECT count(*)::bigint FROM information_schema.views
      WHERE table_schema='public' AND table_name='platform_org_overview'`),
  );
  check('platform_org_overview view', overview === 1);

  // The whole point of the design: a platform admin must NOT be able to read
  // occupancy. Assert no platform policy exists on any measurement table.
  const leakyPolicies = await prisma.$queryRawUnsafe<{ tablename: string; policyname: string }[]>(
    `SELECT tablename, policyname FROM pg_policies
     WHERE schemaname='public'
       AND tablename IN ('zone_minute_stats','zone_day_stats','alerts','alert_rules','reports','audit_logs')
       AND (COALESCE(qual,'') || COALESCE(with_check,'')) LIKE '%is_platform_admin%'`,
  );
  check('NO platform access to occupancy data', leakyPolicies.length === 0,
    leakyPolicies.length
      ? `LEAK via ${leakyPolicies.map((p) => `${p.tablename}.${p.policyname}`).join(', ')}`
      : 'measurement tables have no platform policy');

  // Platform metadata policies should exist where they are intended to.
  const metaPolicies = Number(
    await one<bigint>(`SELECT count(*)::bigint FROM pg_policies
      WHERE schemaname='public'
        AND (COALESCE(qual,'') || COALESCE(with_check,'')) LIKE '%is_platform_admin%'`),
  );
  check('platform metadata policies present', metaPolicies >= 8, `${metaPolicies} found`);

  // Privilege escalation must not be reachable from the API role.
  const grantExec = Number(
    await one<bigint>(`SELECT count(*)::bigint FROM information_schema.routine_privileges
      WHERE routine_schema='public'
        AND routine_name IN ('grant_platform_admin','revoke_platform_admin')
        AND grantee IN ('authenticated','anon')`),
  );
  check('grant/revoke NOT callable by API roles', grantExec === 0, `${grantExec} grants`);

  // platform_admins must be read-only through the API — no INSERT policy.
  const paWrite = Number(
    await one<bigint>(`SELECT count(*)::bigint FROM pg_policies
      WHERE schemaname='public' AND tablename='platform_admins' AND cmd <> 'SELECT'`),
  );
  check('platform_admins has no write policy', paWrite === 0, `${paWrite} found`);

  const paAuditMut = Number(
    await one<bigint>(`SELECT count(*)::bigint FROM pg_policies
      WHERE schemaname='public' AND tablename='platform_audit_logs'
        AND cmd IN ('UPDATE','DELETE','ALL')`),
  );
  check('platform audit append-only', paAuditMut === 0, `${paAuditMut} found`);

  // ── Summary ──
  console.log(`\n${pass} passed, ${fail} failed\n`);
  if (fail > 0) process.exitCode = 1;
}

main()
  .catch((e) => {
    console.error('Verification failed:', e instanceof Error ? e.message : e);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
