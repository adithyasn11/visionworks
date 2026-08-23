# VisionWorks — database layer

Prisma **7.9.1** · PostgreSQL 17 (Supabase)

Everything here is **ready but not pushed**. Nothing has touched your Supabase
project yet.

---

## Files

| File | What it is |
|---|---|
| `schema.prisma` | 13 tables, 15 enums, 52 indexes. The single source of truth. |
| `../prisma.config.ts` | Prisma 7 config — connection strings live here now, not in the schema. |
| `sql/001_constraints.sql` | 30 CHECK constraints + the last-admin trigger. Guards the invariants Prisma can't express. |
| `sql/002_auth_triggers.sql` | Bridges Supabase `auth.users` → `profiles`. Auto-accepts invites. `create_organisation()`. Role helper functions. |
| `sql/003_rls_policies.sql` | Row Level Security. Replaces the old `USING (true)` policies. |
| `sql/004_secrets_and_retention.sql` | RTSP credential protection, day rollups, retention job. |
| `sql/005_platform_admin.sql` | `platform_admins` table, `is_platform_admin()`, operator policies. |
| `sql/006_fix_cameras_safe.sql` | Camera view/policy correction. |
| `sql/007_operator_revoke.sql` | `revoke_operator()` with the last-operator lockout guard. |
| `sql/008_uuid_defaults.sql` | **`DEFAULT gen_random_uuid()` on every generated `id`.** Prisma's `@default(uuid())` is client-side only, so PostgREST inserts (every Supabase Server Action) failed with a NOT NULL violation on `id`. |
| `sql/009_dashboard_analytics.sql` | Dashboard aggregation in SQL. PostgREST caps responses at 1000 rows, so folding buckets in JS silently produced wrong numbers. |
| `sql/010_alert_update_role.sql` | Restricts alert acknowledgement to ADMIN + MANAGER (`alert_update` had allowed VIEWER). |
| `sql/011_retention_schedule.sql` | pg_cron jobs: day rollup 02:45, retention purge 03:15, report expiry 03:30 UTC. |
| `sql/012_dashboard_covering_index.sql` | INCLUDE index so wide-window aggregations run index-only; fixes a cold-cache timeout on first load. |
| `seed.ts` | Generates ~826,000 minute buckets of realistic shaped data. |

**Order matters.** 001 → 002 → … → 010 → 011 → 012. Later files
depend on earlier ones — 004's column grants must run after 003's table grants,
or the RTSP credential becomes readable again.

---

## What was verified

Applied to a real PostgreSQL 17 container and tested. Not just "it compiles".

| Check | Result |
|---|---|
| `prisma validate` | ✅ valid |
| `prisma generate` | ✅ client generated |
| All 4 SQL files parse (libpg_query) | ✅ 126 statements |
| All 4 apply to Postgres 17 | ✅ clean |
| Signup trigger → profile | ✅ 4/4 (incl. Google metadata keys) |
| `create_organisation()` atomicity | ✅ 6/6 (org + site + admin + audit) |
| Invite auto-acceptance | ✅ 3/3 |
| Last-admin protection | ✅ demote and delete both blocked |
| CHECK constraints | ✅ 23/23 bad writes rejected |
| RLS tenant isolation | ✅ 19/19 — zero cross-tenant leaks |
| Day rollup correctness | ✅ 61 min → 11.3% of a 540-min workday |
| Rollup idempotency | ✅ re-run produces one row, not two |
| Retention purge | ✅ 63 buckets deleted, day rollups survived |
| Seed | ✅ 824,312 buckets written |
| Index usage | ✅ `orgId_bucketStart_idx` used on the analytics query |
| Analytics query on 824k rows | ✅ **128 ms** (was 8,623 ms before the RLS rewrite) |
| Unindexed foreign keys | ✅ 0 remaining |
| `seed.ts` under `tsc --strict` | ✅ no errors |

**Total: 55 behavioural assertions, 0 failures.**

Four real problems were caught and fixed:

1. **RTSP credential was readable.** A table-level `GRANT SELECT` in 003
   silently overrode the column-level `REVOKE` in 004 — Postgres treats a table
   grant as covering every column, so the revoke appeared to succeed and did
   nothing. Fixed by granting each permitted column explicitly and never
   holding a table-wide SELECT on `cameras`.

2. **Seed violated its own constraint.** Jittering the three posture weights
   independently could push `sit + stand + walk` past `sampleFrames`. The
   constraint caught it, which is the constraint doing its job.

3. **RLS policies were 68× too slow.** Every policy called a boolean helper —
   `USING (can_read_org("orgId"))` — which Postgres evaluates once per
   candidate row. `EXPLAIN ANALYZE` showed `loops=824312`. Marking the function
   `STABLE` does not help, and neither does the commonly-recommended
   `(select ...)` wrapper (measured: 25.1s vs 25.5s — no change).

   The fix is to make the predicate a set membership test the planner can
   materialise once: `USING ("orgId" IN (SELECT public.user_org_ids()))`.

   ```
   boolean helper   8,623 ms   loops=824312
   IN-list            128 ms   loops=1        68x faster, identical rows
   ```

   All 39 policies were rewritten. The 55 security tests still pass — this is
   purely a planning change, not a loosening of the boundary.

4. **13 unindexed foreign keys.** Postgres does not index FKs automatically, so
   `ON DELETE CASCADE` was scanning whole tables while holding a lock —
   including `ZoneMinuteStat.cameraId` on 824k rows, which would turn "delete a
   camera" into a multi-second stall. Index count went 39 → 52.

---

## Applying it (when you're ready)

### 1. Get your connection strings

Supabase → Settings → Database → Connection string. You need **both**:

```
DATABASE_URL   port 6543   PgBouncer pooler — app queries
DIRECT_URL     port 5432   direct session  — migrations
```

They are not interchangeable. `prisma migrate` takes advisory locks that a
transaction-mode pooler cannot hold. URL-encode special characters in the
password (`@` → `%40`, `#` → `%23`).

Put both in `frontend/.env.local`. Neither gets a `NEXT_PUBLIC_` prefix — they
carry the database password.

### 2. Create the tables

```bash
cd frontend
npx prisma migrate dev --name init
```

### 3. Apply the SQL, in order

Supabase Dashboard → SQL Editor, paste each file:

```
prisma/sql/001_constraints.sql
prisma/sql/002_auth_triggers.sql
prisma/sql/003_rls_policies.sql
prisma/sql/004_secrets_and_retention.sql
```

### 4. Optional — load demo data

```bash
npx tsx prisma/seed.ts
```

Only ever touches organisations whose slug starts with `demo-`. It cannot
delete real data. Use `--wipe` to regenerate.

### 5. Schedule the nightly job

Enable `pg_cron` (Database → Extensions), then:

```sql
select cron.schedule('vw-nightly', '15 2 * * *', $$
  select public.rollup_zone_day_stats((now() - interval '1 day')::date);
  select public.purge_expired_minute_stats();
  select public.expire_stale_reports();
$$);
```

Order matters: roll up **before** purging, or the rollup finds the minutes
already deleted and writes zeros over good history.

---

## Prisma 7 notes

Three things changed from Prisma 6, and all three will bite you if you follow
an older tutorial:

1. **`url` / `directUrl` are gone from `schema.prisma`.** They live in
   `prisma.config.ts`. The config's datasource accepts only `url` and
   `shadowDatabaseUrl` — there is no `directUrl` key at all.

2. **`new PrismaClient()` throws.** A driver adapter is now required:

   ```ts
   import { PrismaPg } from '@prisma/adapter-pg';
   const prisma = new PrismaClient({
     adapter: new PrismaPg({ connectionString: process.env.DATABASE_URL }),
   });
   ```

3. **`migrate diff --to-schema-datamodel` was renamed** to `--to-schema`.

---

## Why the SQL Editor shows all 824,312 rows

Run this in the Supabase dashboard and you get the full count, not zero:

```sql
select count(*) from public.zone_minute_stats;   -- 824312
```

**That is not a broken policy.** The SQL Editor connects as `postgres`, and on
Supabase that role holds `rolbypassrls = true`. RLS is skipped for it entirely —
by design, so you can never lock yourself out of your own database.

Verified on the live database, same query, same moment:

| Connected as | Rows visible |
|---|---|
| `postgres` (has BYPASSRLS) | 824,312 |
| `authenticated`, platform admin | **0** |
| `authenticated`, org admin | own org only |

Your app connects as `authenticated`, so tenant isolation and the platform
boundary both hold. `prisma/verify.ts` and the 25 platform tests assert the
`authenticated` path, which is the one that matters.

**To check a policy from the dashboard**, impersonate the role instead of
querying as `postgres`:

```sql
begin;
  set local role authenticated;
  select set_config('request.jwt.claim.sub', '<a-profile-uuid>', true);
  select count(*) from public.zone_minute_stats;   -- now RLS applies
rollback;
```

Two dead ends worth knowing, both tested rather than assumed:

- `ALTER TABLE ... FORCE ROW LEVEL SECURITY` does **not** help. `FORCE` closes
  the *owner* exemption but is overridden by `BYPASSRLS`, so on Supabase it
  changes nothing.
- On a plain Postgres where the owner lacks `BYPASSRLS`, forcing RLS makes
  writes **silently no-op** (`INSERT 0 0`) rather than error — the seed would
  report success and write nothing. Not a trade worth making.

## Things to know

**Users are not seeded.** Profiles come from real Supabase signups — the
trigger in 002 creates them. Sign up through your existing `/signup` page, then
call `create_organisation()` from `/onboarding`.

**The `service_role` key bypasses all of this.** RLS does not apply to it, by
design — the Python CV backend needs it to write buckets. That key must never
reach the browser. It is the one credential that defeats every policy here.

**Three data layers still overlap.** `workplace_analytics.db` (SQLite),
`backend/app/db/models.py` (SQLAlchemy) and this schema all describe the same
domain. Retiring the first two is Phase 1 work; until then, treat this as the
source of truth and the others as legacy.

---

## Built-in demo cases

The seed deliberately includes edge cases so features have something to work
against:

| Zone | Purpose |
|---|---|
| Desk Bank C — 12% utilised | The "reclaim this space" insight |
| Meeting Room 2 — peaks of 6 against capacity 4 | OVERCROWDING alerts |
| Main Corridor — null capacity, 88% walking | Null-capacity handling, transit semantics |
| Meridian Coworking — second tenant | RLS isolation tests |
| 2 sessions ERROR / CANCELLED | Non-happy-path UI |

Verified shape of the generated data:

```
hour  avg occupancy
 09   ####################      double peak
 10   #######################
 12   #########                 lunch dip
 14   ########################
 17   ######

Mon 2.48  Tue 3.16  Wed 3.19  Thu 3.05  Fri 2.01  Sat 0.86   hybrid working
```
