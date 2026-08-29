-- VisionWorks — Phase E, Steps 15/16
-- Hourly rollups, and per-person visibility for measured data.
--
-- Two changes that belong together, because the second is what makes the
-- first safe to build a team view on.
--
--
-- 1. WHY AN HOURLY TABLE
--
-- `employee_day_stats` holds one row per person per day. That answers "how
-- long was Prajwal at his desk on Tuesday" but cannot answer "when" — and a
-- daily timeline is the whole point of Step 15.
--
-- The raw per-observation rows (`identity_events`) do hold that shape, but
-- they are written to local SQLite and never synced here: the table exists in
-- Postgres for the pipeline's own use, and its `synced_at` column has no
-- writer. Reading them from a browser is therefore not possible today, and
-- syncing them would be the wrong fix regardless — at a sample every five
-- seconds that is ~17k rows per person per day, pushed across the network so
-- the client can immediately average them back down into 24 buckets.
--
-- So the aggregation happens where the data already is, and what crosses the
-- network is 24 rows. Same pattern as the daily rollup, one level finer.
--
--
-- 2. WHY VISIBILITY CHANGES
--
-- Until now `employee_day_stats` returned every row to every member of the
-- org: `USING ("orgId" IN (SELECT user_org_ids()))`. That was defensible while
-- the only reader was a per-employee page somebody had to navigate to
-- deliberately. Step 16 puts the whole team in one sortable table, and at that
-- point "any member can see everyone's desk time" becomes a surveillance
-- feature nobody asked for.
--
-- The rule this migration installs:
--
--   ADMIN, MANAGER  → every employee in their organisation
--   VIEWER          → only the rows for the employee they are linked to
--
-- Which requires knowing which employee a login IS — a link that did not
-- exist, because until now `employees` was a roster of people to recognise on
-- camera, with no notion that any of them might also hold an account.
--
--
-- 3. WHAT THIS DOES NOT DO
--
-- It does not hide someone's existence: `employees` itself stays readable to
-- the whole org, because the roster is how a manager assigns desks and how
-- anyone understands who the system may name. What becomes private is the
-- MEASUREMENT — the hours, the chair exits, the focus blocks.
--
-- Idempotent throughout: safe to re-run.

BEGIN;

-- ══════════════════════════════════════════════════════════════════════════
-- PART 0 — UUID DEFAULTS FOR THE IDENTITY TABLES
-- ══════════════════════════════════════════════════════════════════════════
--
-- Migration 008 documented this trap in full and fixed it for every table that
-- existed then: Prisma's `@default(uuid())` generates the id in JavaScript, so
-- `prisma migrate` creates the column as plain `uuid NOT NULL` with no DEFAULT.
-- Any writer that is not Prisma — a PostgREST insert from a Server Action, a
-- raw SQL backfill, the backend's service-role sync — hits
--
--   null value in column "id" ... violates not-null constraint
--
-- The identity tables arrived in 020/021, after 008 ran, so they never got the
-- repair. 020 does declare `DEFAULT gen_random_uuid()`, but its
-- CREATE TABLE IF NOT EXISTS is a no-op on a database where Prisma already
-- created the table — which is every environment here. Measured on a fresh
-- build of this schema: employees.id had no default at all.
--
-- Safe with Prisma, for the reason 008 gives: a DEFAULT only applies when the
-- INSERT omits the column, and Prisma always supplies its own v4 UUID.
ALTER TABLE public.employees            ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE public.employee_day_stats   ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE public.identity_events      ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE public.face_templates       ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE public.camera_links         ALTER COLUMN id SET DEFAULT gen_random_uuid();

-- ══════════════════════════════════════════════════════════════════════════
-- PART 1 — LINK AN EMPLOYEE TO A LOGIN
-- ══════════════════════════════════════════════════════════════════════════

-- NULL means "this person is on camera but has no account", which is the
-- common case — most people being measured never sign in at all.
--
-- ON DELETE SET NULL, not CASCADE: deleting a login must not delete the
-- employment record or the history attached to it. Someone can lose their
-- account and still have worked here.
ALTER TABLE public.employees
  ADD COLUMN IF NOT EXISTS "profileId" UUID
    REFERENCES public.profiles(id) ON DELETE SET NULL;

COMMENT ON COLUMN public.employees."profileId" IS
  'The login this employee is, if any. NULL for people who are measured but '
  'have no account. Drives per-person visibility of measured data: a VIEWER '
  'sees only the day/hour stats of the employee row that points at them.';

-- One account cannot be two employees. Without this, a duplicate link would
-- silently widen what a VIEWER can see — they would read both employees'
-- figures and the policy would be behaving exactly as written.
--
-- Partial, so the many NULLs do not collide.
CREATE UNIQUE INDEX IF NOT EXISTS ux_employees_profile
  ON public.employees ("profileId")
  WHERE "profileId" IS NOT NULL;

-- The self-lookup below runs on every row of every policy check, so it needs
-- to be an index hit and not a scan.
CREATE INDEX IF NOT EXISTS ix_employees_profile_org
  ON public.employees ("profileId", "orgId")
  WHERE "deletedAt" IS NULL;

-- The employee ids the caller IS.
--
-- Set-returning, not boolean, for the reason documented at length in 002: a
-- boolean helper in a policy's USING clause is re-evaluated per candidate row
-- (measured there at 8.6s vs 126ms on 824k rows). `x IN (SELECT ...)` lets the
-- planner hash the set once.
--
-- Normally one row or none. It returns a set rather than a scalar so the
-- policy reads the same as the org helpers beside it, and so a future
-- multi-org account does not need this rewritten.
CREATE OR REPLACE FUNCTION public.my_employee_ids()
RETURNS SETOF UUID
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT id
  FROM public.employees
  WHERE "profileId" = (SELECT auth.uid())
    AND "deletedAt" IS NULL;
$$;

COMMENT ON FUNCTION public.my_employee_ids() IS
  'Employee rows the caller is linked to. SECURITY DEFINER so it resolves '
  'before employee_select applies — a policy that consulted a table guarded '
  'by another policy would recurse.';

GRANT EXECUTE ON FUNCTION public.my_employee_ids() TO authenticated;

-- ══════════════════════════════════════════════════════════════════════════
-- PART 2 — HOURLY ROLLUP
-- ══════════════════════════════════════════════════════════════════════════

-- One row per employee per hour. Written by the backend through the service
-- role; never by a browser (there is no INSERT/UPDATE/DELETE policy at all,
-- and `authenticated` holds only SELECT).
--
-- That asymmetry is the point. A figure about somebody's working day that a
-- manager could quietly adjust would be worth nothing.
CREATE TABLE IF NOT EXISTS public.employee_hour_stats (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "orgId"             UUID NOT NULL REFERENCES public.organisations(id) ON DELETE CASCADE,
  "employeeId"        UUID NOT NULL REFERENCES public.employees(id) ON DELETE CASCADE,
  "statDate"          DATE NOT NULL,
  -- 0-23, in the same timezone the daily rollup uses. Stored as a plain
  -- integer rather than a timestamp because it is a BUCKET, not an instant:
  -- "hour 14" means 14:00-14:59, and a timestamp would invite readers to
  -- treat it as a moment.
  hour                SMALLINT NOT NULL CHECK (hour BETWEEN 0 AND 23),

  -- Minutes, capped at 60 by construction. Anything above that would mean the
  -- aggregator double-counted overlapping observations, so the constraint is
  -- a real check on the writer rather than defensive decoration.
  "presentMinutes"    INTEGER NOT NULL DEFAULT 0 CHECK ("presentMinutes"  BETWEEN 0 AND 60),
  "deskMinutes"       INTEGER NOT NULL DEFAULT 0 CHECK ("deskMinutes"     BETWEEN 0 AND 60),
  "seatedMinutes"     INTEGER NOT NULL DEFAULT 0 CHECK ("seatedMinutes"   BETWEEN 0 AND 60),
  -- Time somebody was observed but not confidently identified. Step 14's
  -- abstention, preserved at hour resolution so the timeline can show WHEN
  -- the system was unsure rather than only how much.
  "unknownMinutes"    INTEGER NOT NULL DEFAULT 0 CHECK ("unknownMinutes"  BETWEEN 0 AND 60),
  "awayFromDeskCount" INTEGER NOT NULL DEFAULT 0 CHECK ("awayFromDeskCount" >= 0),

  -- Mean attribution confidence for this hour's attributed samples.
  --
  -- Per hour, not inherited from the day: an hour at 0.95 and an hour at 0.55
  -- averaged into one daily 0.75 hides that half the day is unreliable. The
  -- whole reason Step 14 exists is that a confidence hidden inside an average
  -- stops being a warning.
  "bindingConfidence" DOUBLE PRECISION NOT NULL DEFAULT 0
    CHECK ("bindingConfidence" BETWEEN 0 AND 1),

  "createdAt"         TIMESTAMPTZ(6) NOT NULL DEFAULT NOW(),
  "updatedAt"         TIMESTAMPTZ(6) NOT NULL DEFAULT NOW(),

  -- The upsert key. A re-run of the aggregator must replace an hour, never
  -- append a second copy of it.
  CONSTRAINT employee_hour_stats_unique UNIQUE ("employeeId", "statDate", hour)
);

COMMENT ON TABLE public.employee_hour_stats IS
  'Per-employee hourly rollup, one level finer than employee_day_stats. Feeds '
  'the Step 15 daily timeline. Written only by the pipeline via service_role.';

-- The dashboard's actual query: one employee, one day, ordered by hour.
CREATE INDEX IF NOT EXISTS ix_ehs_employee_date
  ON public.employee_hour_stats ("employeeId", "statDate", hour);

-- The org-wide sweep behind the team view and any future org report.
CREATE INDEX IF NOT EXISTS ix_ehs_org_date
  ON public.employee_hour_stats ("orgId", "statDate");

ALTER TABLE public.employee_hour_stats ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.employee_hour_stats FORCE ROW LEVEL SECURITY;

-- ══════════════════════════════════════════════════════════════════════════
-- PART 3 — VISIBILITY
-- ══════════════════════════════════════════════════════════════════════════
--
-- Both policies are the same shape:
--
--   in an org I manage           → every row
--   OR about an employee I am    → my own rows
--
-- Written as one OR rather than two policies because multiple permissive
-- policies on the same command are OR-ed anyway, and one expression the
-- planner can see whole optimises better than two it must union.
--
-- `manage_org_ids()` is ADMIN + MANAGER, which is exactly the "manager and
-- admin see everyone" rule — reusing it means this cannot drift out of step
-- with the rest of the permission model.

DROP POLICY IF EXISTS employee_day_stat_select ON public.employee_day_stats;
CREATE POLICY employee_day_stat_select ON public.employee_day_stats
  FOR SELECT TO authenticated
  USING (
    "orgId" IN (SELECT public.manage_org_ids())
    OR "employeeId" IN (SELECT public.my_employee_ids())
  );

DROP POLICY IF EXISTS employee_hour_stat_select ON public.employee_hour_stats;
CREATE POLICY employee_hour_stat_select ON public.employee_hour_stats
  FOR SELECT TO authenticated
  USING (
    "orgId" IN (SELECT public.manage_org_ids())
    OR "employeeId" IN (SELECT public.my_employee_ids())
  );

-- `identity_events` is the raw per-observation stream — finer-grained than
-- either rollup, and it was readable org-wide. Narrow it the same way, or the
-- restriction above is theatre: a VIEWER blocked from the hourly table could
-- reconstruct it from the events.
DROP POLICY IF EXISTS identity_event_select ON public.identity_events;
CREATE POLICY identity_event_select ON public.identity_events
  FOR SELECT TO authenticated
  USING (
    "orgId" IN (SELECT public.manage_org_ids())
    OR "employeeId" IN (SELECT public.my_employee_ids())
  );

-- ══════════════════════════════════════════════════════════════════════════
-- PART 4 — GRANTS
-- ══════════════════════════════════════════════════════════════════════════
--
-- A policy only filters rows the role may already read. Migration 004
-- established the trap this repeats: a table-level GRANT silently supersedes
-- a column-level REVOKE, so grants are stated explicitly rather than assumed.

GRANT SELECT ON public.employee_hour_stats TO authenticated;

-- `profileId` is written by an admin through the roster UI, so the existing
-- table-level GRANT on `employees` already covers it. Stated here so the
-- next person reading this file does not have to go and check.
--   GRANT SELECT, INSERT, UPDATE, DELETE ON public.employees TO authenticated;  (020)

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.employee_hour_stats TO service_role;
  END IF;
END $$;

COMMIT;
