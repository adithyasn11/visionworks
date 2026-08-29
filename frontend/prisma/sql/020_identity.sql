-- VisionWorks — per-employee identity tracking
--
-- Adds the five tables that turn the anonymous pipeline into one that can say
-- WHO, not just how many. Step 2 of IDENTITY_TRACKING_PLAN.md.
--
-- ═══════════════════════════════════════════════════════════════════════════
--  WHAT THIS DOES NOT TOUCH
-- ═══════════════════════════════════════════════════════════════════════════
--
-- `zone_minute_stats`, `zone_day_stats` and every existing table are left
-- exactly as they are. The anonymous path keeps working unchanged — this is a
-- PARALLEL set of tables, not a migration of the old ones. That is deliberate:
-- `minute_aggregator.py` discards track ids by design (it is what makes the
-- existing analytics non-identifying), and per-employee data must not be
-- retrofitted into a structure whose whole point is that it cannot identify
-- anyone.
--
-- ═══════════════════════════════════════════════════════════════════════════
--  THE PRIVACY POSTURE, STATED PLAINLY
-- ═══════════════════════════════════════════════════════════════════════════
--
-- These tables identify named people, which is a materially different thing
-- from counting bodies in a zone. Three structural decisions follow from that:
--
--   1. `face_templates` stores EMBEDDINGS ONLY, never photographs. An ArcFace
--      vector is not reversible to a usable face image, so a leak of this
--      table does not leak anyone's likeness. Enrolment photos are processed
--      in memory and discarded.
--
--   2. `face_templates` is readable by NOBODY through the API. There is a
--      SELECT policy for admins covering only the non-biometric columns via a
--      view; the raw `embedding` column is revoked from `authenticated`
--      entirely. Only the service role (the CV backend) can read vectors.
--      This mirrors how 004 protects RTSP credentials.
--
--   3. `identity_events` is high-volume and short-lived (7-day default
--      retention, purged by the same pg_cron job pattern as 011). The durable
--      record is `employee_day_stats`, which is aggregate, not per-frame.
--
-- ═══════════════════════════════════════════════════════════════════════════
--  RLS MODEL — mirrors 003 exactly
-- ═══════════════════════════════════════════════════════════════════════════
--
--   read   -> "orgId" IN (SELECT user_org_ids())    ADMIN, MANAGER, VIEWER
--   write  -> "orgId" IN (SELECT manage_org_ids())  ADMIN, MANAGER
--   govern -> "orgId" IN (SELECT admin_org_ids())   ADMIN
--
-- Every predicate is `"orgId" IN (SELECT ...)` rather than a boolean helper,
-- for the reason 003 documents and measured: a boolean function in a policy is
-- evaluated once per candidate row, a set-returning function once per query.
-- On `identity_events`, which will be the largest table here, that is the
-- difference between a usable page and a timeout.
--
-- ═══════════════════════════════════════════════════════════════════════════
--  MEASURED DATA IS READ-ONLY TO BROWSERS
-- ═══════════════════════════════════════════════════════════════════════════
--
-- `identity_events` and `employee_day_stats` get SELECT policies and no
-- INSERT/UPDATE/DELETE policies at all, exactly like `zone_minute_stats`. They
-- are written only by the CV pipeline through the service role, which bypasses
-- RLS. A browser client cannot fabricate attendance for anyone — which, for a
-- table that says who was at their desk and for how long, is the whole point.
--
-- `employees`, `face_templates` and `camera_links` ARE configuration, so
-- managers may write them.
--
-- Idempotent: every object is created IF NOT EXISTS, and policies are dropped
-- before being recreated, so this file is safe to re-run.
--
-- Apply AFTER 019_org_delete_cleanup.sql.

-- ═══════════════════════════════════════════════════════════════════════════
--  TABLES
-- ═══════════════════════════════════════════════════════════════════════════

-- A person the system is allowed to name.
--
-- `assignedZoneId` is the seat prior — the single strongest identity signal in
-- a fixed-desk office (85-95% alone, per the plan's §3 table) and the only one
-- that costs no biometrics at all. ON DELETE SET NULL rather than CASCADE:
-- deleting a zone must not delete the employee who sat in it.
CREATE TABLE IF NOT EXISTS public.employees (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "orgId"        UUID NOT NULL REFERENCES public.organisations(id) ON DELETE CASCADE,
  "employeeCode" VARCHAR(64)  NOT NULL,
  "displayName"  VARCHAR(160) NOT NULL,
  "assignedZoneId" UUID REFERENCES public.zones(id) ON DELETE SET NULL,
  active         BOOLEAN NOT NULL DEFAULT TRUE,
  "createdAt"    TIMESTAMPTZ(6) NOT NULL DEFAULT NOW(),
  "updatedAt"    TIMESTAMPTZ(6) NOT NULL DEFAULT NOW(),
  -- Soft delete, matching cameras/zones/sites. Identity history must survive
  -- an employee leaving, or last quarter's reports change retroactively.
  "deletedAt"    TIMESTAMPTZ(6)
);

-- Enrolment templates. EMBEDDINGS ONLY — see the privacy note above.
-- 3-5 rows per person; `quality` is the detector confidence at capture, used
-- to reject enrolments that would poison every later match (plan §8.4).
CREATE TABLE IF NOT EXISTS public.face_templates (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "employeeId" UUID NOT NULL REFERENCES public.employees(id) ON DELETE CASCADE,
  -- 512-d ArcFace vector. DOUBLE PRECISION[] rather than pgvector so this
  -- migration has no extension dependency; the gallery is per-org and small
  -- (hundreds of rows), so brute-force cosine in the backend is fine. Switching
  -- to pgvector later is an ALTER, not a redesign.
  embedding    DOUBLE PRECISION[] NOT NULL,
  quality      DOUBLE PRECISION   NOT NULL,
  "createdAt"  TIMESTAMPTZ(6) NOT NULL DEFAULT NOW()
);

-- Raw per-observation identity attributions. High volume, short retention.
-- The per-person analogue of activity_logs.
--
-- `employeeId` NULL is not missing data — it is the system correctly declining
-- to guess. The plan's central rule: below the confidence floor, output
-- UNKNOWN rather than the nearest name.
CREATE TABLE IF NOT EXISTS public.identity_events (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "orgId"      UUID NOT NULL REFERENCES public.organisations(id) ON DELETE CASCADE,
  -- CASCADE, not SET NULL. SET NULL would blank `employeeId` on a hard delete
  -- while `method` still said 'fusion', which the honesty constraint below
  -- forbids — measured: deleting an employee with any event raised
  -- "violates check constraint identity_events_unknown_consistent", making the
  -- employee_delete policy unusable in practice.
  --
  -- CASCADE is also the RIGHT answer independently of that. A NULLed row would
  -- be indistinguishable from a genuine UNKNOWN, so a hard erasure would
  -- silently inflate the abstention rate that Step 17 measures. A hard delete
  -- is the erasure path (the UI uses soft delete precisely to keep history);
  -- when someone is truly erased, their observations go with them.
  "employeeId" UUID REFERENCES public.employees(id) ON DELETE CASCADE,
  "cameraId"   UUID NOT NULL REFERENCES public.cameras(id) ON DELETE CASCADE,
  "zoneId"     UUID REFERENCES public.zones(id) ON DELETE SET NULL,
  -- The ByteTrack fragment id. Namespaced per session by `identityId`, because
  -- ByteTrack restarts at 0 for every video (plan §8.5) — track 3 today is not
  -- track 3 tomorrow.
  "trackId"    INTEGER NOT NULL,
  "identityId" VARCHAR(64) NOT NULL,
  posture      public."Posture" NOT NULL,
  confidence   DOUBLE PRECISION NOT NULL,
  -- face | fusion | seat | handoff | unknown
  method       VARCHAR(24) NOT NULL,
  "observedAt" TIMESTAMPTZ(6) NOT NULL
);

-- Daily rollup: one row per employee per day. This is what the UI reads.
CREATE TABLE IF NOT EXISTS public.employee_day_stats (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "orgId"             UUID NOT NULL REFERENCES public.organisations(id) ON DELETE CASCADE,
  "employeeId"        UUID NOT NULL REFERENCES public.employees(id) ON DELETE CASCADE,
  "statDate"          DATE NOT NULL,

  "firstSeenAt"       TIMESTAMPTZ(6),
  "lastSeenAt"        TIMESTAMPTZ(6),
  "presentMinutes"    INTEGER NOT NULL DEFAULT 0,
  "deskMinutes"       INTEGER NOT NULL DEFAULT 0,
  "seatedMinutes"     INTEGER NOT NULL DEFAULT 0,
  "awayFromDeskCount" INTEGER NOT NULL DEFAULT 0,
  "breakMinutes"      INTEGER NOT NULL DEFAULT 0,
  "longestFocusBlock" INTEGER NOT NULL DEFAULT 0,
  "fragmentationIdx"  DOUBLE PRECISION NOT NULL DEFAULT 0,

  -- Mean fusion confidence across the day. Below 0.6 the UI must present the
  -- row as low-confidence rather than as fact. A number without its confidence
  -- is a claim that cannot be defended.
  "bindingConfidence" DOUBLE PRECISION NOT NULL DEFAULT 0,
  -- Time observed but not attributable to anyone. This is the honest residue;
  -- it must never be silently folded into the nearest employee's total.
  "unknownMinutes"    INTEGER NOT NULL DEFAULT 0,

  "createdAt"         TIMESTAMPTZ(6) NOT NULL DEFAULT NOW(),
  "updatedAt"         TIMESTAMPTZ(6) NOT NULL DEFAULT NOW()
);

-- Physical topology: which camera exits lead to which camera entries, and how
-- long the walk plausibly takes. Powers the cross-camera handoff check.
CREATE TABLE IF NOT EXISTS public.camera_links (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "orgId"        UUID NOT NULL REFERENCES public.organisations(id) ON DELETE CASCADE,
  "fromCameraId" UUID NOT NULL REFERENCES public.cameras(id) ON DELETE CASCADE,
  "toCameraId"   UUID NOT NULL REFERENCES public.cameras(id) ON DELETE CASCADE,
  "minSeconds"   INTEGER NOT NULL,
  "maxSeconds"   INTEGER NOT NULL,
  "createdAt"    TIMESTAMPTZ(6) NOT NULL DEFAULT NOW()
);

-- ═══════════════════════════════════════════════════════════════════════════
--  CONSTRAINTS
-- ═══════════════════════════════════════════════════════════════════════════
--
-- The same posture as 001_constraints.sql: express in the database every
-- invariant the database is capable of expressing, so a bug in one writer
-- cannot corrupt what every reader assumes.
--
-- DO blocks because ADD CONSTRAINT has no IF NOT EXISTS in Postgres 17.

-- Repair the FK on a database created by an earlier draft of this file, where
-- `identity_events."employeeId"` was ON DELETE SET NULL. CREATE TABLE IF NOT
-- EXISTS leaves an existing table alone, so the fix has to be explicit or the
-- conflict above survives the re-run.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'identity_events_employeeId_fkey'
      AND conrelid = 'public.identity_events'::regclass
      AND confdeltype <> 'c'          -- 'c' = CASCADE
  ) THEN
    ALTER TABLE public.identity_events
      DROP CONSTRAINT "identity_events_employeeId_fkey";
    ALTER TABLE public.identity_events
      ADD CONSTRAINT "identity_events_employeeId_fkey"
      FOREIGN KEY ("employeeId") REFERENCES public.employees(id) ON DELETE CASCADE;
  END IF;
END $$;

DO $$
BEGIN
  -- An employee code identifies a person WITHIN an organisation, and must stay
  -- reusable after the employee is soft-deleted — otherwise a departed
  -- "E-014" blocks the code forever, which is exactly the bug 019 fixed for
  -- organisation slugs. Partial unique index, not a table constraint.
  IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relname = 'employees_org_code_key') THEN
    CREATE UNIQUE INDEX employees_org_code_key
      ON public.employees ("orgId", "employeeCode")
      WHERE "deletedAt" IS NULL;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'employees_code_not_blank') THEN
    ALTER TABLE public.employees ADD CONSTRAINT employees_code_not_blank
      CHECK (length(btrim("employeeCode")) > 0);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'employees_name_not_blank') THEN
    ALTER TABLE public.employees ADD CONSTRAINT employees_name_not_blank
      CHECK (length(btrim("displayName")) > 0);
  END IF;

  -- A quality score outside [0,1] means the enrolment path is broken; storing
  -- it would let a garbage template win a cosine comparison.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'face_templates_quality_range') THEN
    ALTER TABLE public.face_templates ADD CONSTRAINT face_templates_quality_range
      CHECK (quality >= 0 AND quality <= 1);
  END IF;

  -- An empty embedding is not a template. Guards against a failed extraction
  -- being written as a valid row.
  --
  -- cardinality(), NOT array_length(). For an empty array array_length(a, 1)
  -- returns NULL rather than 0, `NULL > 0` is NULL, and a CHECK treats NULL as
  -- SATISFIED — so the obvious spelling of this constraint accepts exactly the
  -- value it exists to reject. Measured on Postgres 17.11: the empty-array
  -- insert succeeded against `array_length(embedding, 1) > 0`.
  -- cardinality() returns 0 for an empty array and the check bites.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'face_templates_embedding_present') THEN
    ALTER TABLE public.face_templates ADD CONSTRAINT face_templates_embedding_present
      CHECK (cardinality(embedding) > 0);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'identity_events_confidence_range') THEN
    ALTER TABLE public.identity_events ADD CONSTRAINT identity_events_confidence_range
      CHECK (confidence >= 0 AND confidence <= 1);
  END IF;

  -- The vocabulary the fusion stage may claim. A method outside this set means
  -- a caller invented one, and the evaluation in Step 17 would silently
  -- mis-bucket it.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'identity_events_method_known') THEN
    ALTER TABLE public.identity_events ADD CONSTRAINT identity_events_method_known
      CHECK (method IN ('face', 'fusion', 'seat', 'handoff', 'unknown'));
  END IF;

  -- UNKNOWN must be honest in BOTH columns. An event with no employee cannot
  -- claim a method that names one, and a named attribution cannot claim to be
  -- unknown. Without this the two columns can disagree and the accuracy
  -- numbers in Step 17 become unreliable.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'identity_events_unknown_consistent') THEN
    ALTER TABLE public.identity_events ADD CONSTRAINT identity_events_unknown_consistent
      CHECK (("employeeId" IS NULL) = (method = 'unknown'));
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'employee_day_stats_employee_date_key') THEN
    ALTER TABLE public.employee_day_stats ADD CONSTRAINT employee_day_stats_employee_date_key
      UNIQUE ("employeeId", "statDate");
  END IF;

  -- Minute counters are durations; a negative one is a bug in the aggregator,
  -- not a value. Bounded above by a day so an overflow cannot silently produce
  -- "27 hours at desk" in a report.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'employee_day_stats_minutes_sane') THEN
    ALTER TABLE public.employee_day_stats ADD CONSTRAINT employee_day_stats_minutes_sane
      CHECK (
        "presentMinutes"    BETWEEN 0 AND 1440 AND
        "deskMinutes"       BETWEEN 0 AND 1440 AND
        "seatedMinutes"     BETWEEN 0 AND 1440 AND
        "breakMinutes"      BETWEEN 0 AND 1440 AND
        "unknownMinutes"    BETWEEN 0 AND 1440 AND
        "longestFocusBlock" BETWEEN 0 AND 1440 AND
        "awayFromDeskCount" >= 0 AND
        "fragmentationIdx"  >= 0
      );
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'employee_day_stats_confidence_range') THEN
    ALTER TABLE public.employee_day_stats ADD CONSTRAINT employee_day_stats_confidence_range
      CHECK ("bindingConfidence" >= 0 AND "bindingConfidence" <= 1);
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'camera_links_from_to_key') THEN
    ALTER TABLE public.camera_links ADD CONSTRAINT camera_links_from_to_key
      UNIQUE ("fromCameraId", "toCameraId");
  END IF;

  -- A camera does not hand off to itself, and the walk window must be ordered
  -- and non-negative or the plausibility test in Step 13 accepts everything.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'camera_links_not_self') THEN
    ALTER TABLE public.camera_links ADD CONSTRAINT camera_links_not_self
      CHECK ("fromCameraId" <> "toCameraId");
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'camera_links_window_ordered') THEN
    ALTER TABLE public.camera_links ADD CONSTRAINT camera_links_window_ordered
      CHECK ("minSeconds" >= 0 AND "maxSeconds" >= "minSeconds");
  END IF;
END $$;

-- ═══════════════════════════════════════════════════════════════════════════
--  INDEXES
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Every foreign key is indexed. Postgres does NOT do this automatically, and
-- an unindexed FK makes both JOINs and ON DELETE CASCADE scan the whole table
-- — the README records "unindexed foreign keys: 0 remaining" as a verified
-- property of this schema, and 020 keeps it true.
--
-- The `orgId` indexes are also the RLS indexes: every policy below filters on
-- "orgId", so these are what stop the tenant check being a sequential scan.

CREATE INDEX IF NOT EXISTS employees_org_active_idx
  ON public.employees ("orgId", active);
CREATE INDEX IF NOT EXISTS employees_assigned_zone_idx
  ON public.employees ("assignedZoneId");

CREATE INDEX IF NOT EXISTS face_templates_employee_idx
  ON public.face_templates ("employeeId");

-- The three read paths for identity_events: "what happened in this org
-- recently", "what did this person do", and "reassemble this stitched track".
CREATE INDEX IF NOT EXISTS identity_events_org_observed_idx
  ON public.identity_events ("orgId", "observedAt" DESC);
CREATE INDEX IF NOT EXISTS identity_events_employee_observed_idx
  ON public.identity_events ("employeeId", "observedAt" DESC);
CREATE INDEX IF NOT EXISTS identity_events_identity_idx
  ON public.identity_events ("identityId");
CREATE INDEX IF NOT EXISTS identity_events_camera_idx
  ON public.identity_events ("cameraId");
CREATE INDEX IF NOT EXISTS identity_events_zone_idx
  ON public.identity_events ("zoneId");

CREATE INDEX IF NOT EXISTS employee_day_stats_org_date_idx
  ON public.employee_day_stats ("orgId", "statDate" DESC);
CREATE INDEX IF NOT EXISTS employee_day_stats_employee_idx
  ON public.employee_day_stats ("employeeId");

CREATE INDEX IF NOT EXISTS camera_links_org_idx
  ON public.camera_links ("orgId");
CREATE INDEX IF NOT EXISTS camera_links_from_idx
  ON public.camera_links ("fromCameraId");
CREATE INDEX IF NOT EXISTS camera_links_to_idx
  ON public.camera_links ("toCameraId");

-- ═══════════════════════════════════════════════════════════════════════════
--  updatedAt MAINTENANCE
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Prisma's @updatedAt is client-side, exactly like @default(uuid()) was before
-- 008. A PostgREST write from a Server Action would leave the column frozen at
-- its insert value. Maintain it in the database so every writer is covered.

CREATE OR REPLACE FUNCTION public.touch_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
  NEW."updatedAt" = NOW();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS employees_touch_updated_at ON public.employees;
CREATE TRIGGER employees_touch_updated_at
  BEFORE UPDATE ON public.employees
  FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

DROP TRIGGER IF EXISTS employee_day_stats_touch_updated_at ON public.employee_day_stats;
CREATE TRIGGER employee_day_stats_touch_updated_at
  BEFORE UPDATE ON public.employee_day_stats
  FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

-- ═══════════════════════════════════════════════════════════════════════════
--  A ZONE MAY ANCHOR AT MOST ONE ACTIVE EMPLOYEE
-- ═══════════════════════════════════════════════════════════════════════════
--
-- The seat prior only identifies anybody if the mapping is unambiguous. Step 7
-- binds an identity to an employee when it spends >60% of its time in zone Z
-- "and exactly one employee has assignedZoneId == Z". If two active employees
-- share a desk zone, that rule silently never fires and desk time quietly
-- stops being attributed — a failure that would look like "the model is bad"
-- rather than "the configuration is ambiguous".
--
-- Enforced here so the UI cannot create the ambiguity in the first place.
-- Partial: inactive and soft-deleted employees are ignored, so reassigning a
-- desk after someone leaves just works.
CREATE UNIQUE INDEX IF NOT EXISTS employees_one_active_per_zone
  ON public.employees ("assignedZoneId")
  WHERE "assignedZoneId" IS NOT NULL AND active AND "deletedAt" IS NULL;

-- ═══════════════════════════════════════════════════════════════════════════
--  ROW LEVEL SECURITY
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE public.employees          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.face_templates     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.identity_events    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.employee_day_stats ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.camera_links       ENABLE ROW LEVEL SECURITY;

-- ── EMPLOYEES ──────────────────────────────────────────────────────────────
-- read: all roles · write: admin + manager. Same shape as cameras and zones:
-- the roster is part of the space's configuration, which a MANAGER runs.

DROP POLICY IF EXISTS employee_select ON public.employees;
CREATE POLICY employee_select ON public.employees
  FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()) AND "deletedAt" IS NULL);

DROP POLICY IF EXISTS employee_insert ON public.employees;
CREATE POLICY employee_insert ON public.employees
  FOR INSERT TO authenticated
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));

DROP POLICY IF EXISTS employee_update ON public.employees;
CREATE POLICY employee_update ON public.employees
  FOR UPDATE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()))
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));

-- Hard DELETE is admin-only, and the UI never uses it — deactivating is the
-- normal path, because removing the row would orphan the day stats that
-- reports for previous months are built from.
DROP POLICY IF EXISTS employee_delete ON public.employees;
CREATE POLICY employee_delete ON public.employees
  FOR DELETE TO authenticated
  USING ("orgId" IN (SELECT public.admin_org_ids()));

-- ── FACE TEMPLATES ─────────────────────────────────────────────────────────
-- Biometric material. The policies here are scoped through the OWNING
-- employee's org, because the table has no "orgId" of its own — denormalising
-- one would create a second source of truth that could disagree with the
-- employee row.
--
-- Note the column-level revoke further down: even an ADMIN cannot read
-- `embedding` through the API. These policies govern the row; the grant
-- governs the vector.

DROP POLICY IF EXISTS face_template_select ON public.face_templates;
CREATE POLICY face_template_select ON public.face_templates
  FOR SELECT TO authenticated
  USING ("employeeId" IN (
    SELECT id FROM public.employees
    WHERE "orgId" IN (SELECT public.manage_org_ids())
  ));

DROP POLICY IF EXISTS face_template_insert ON public.face_templates;
CREATE POLICY face_template_insert ON public.face_templates
  FOR INSERT TO authenticated
  WITH CHECK ("employeeId" IN (
    SELECT id FROM public.employees
    WHERE "orgId" IN (SELECT public.manage_org_ids())
  ));

-- Deleting a template is how someone withdraws consent to be recognised, so it
-- must be reachable — but only by the roles that could enrol them.
DROP POLICY IF EXISTS face_template_delete ON public.face_templates;
CREATE POLICY face_template_delete ON public.face_templates
  FOR DELETE TO authenticated
  USING ("employeeId" IN (
    SELECT id FROM public.employees
    WHERE "orgId" IN (SELECT public.manage_org_ids())
  ));

-- No UPDATE policy: a template is immutable. Re-enrolling means delete + insert,
-- which keeps `createdAt` meaningful as "when this face was captured".

-- ── IDENTITY EVENTS ────────────────────────────────────────────────────────
-- Measured data. SELECT only — written exclusively by the CV pipeline through
-- the service role, exactly like zone_minute_stats.

DROP POLICY IF EXISTS identity_event_select ON public.identity_events;
CREATE POLICY identity_event_select ON public.identity_events
  FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()));

-- ── EMPLOYEE DAY STATS ─────────────────────────────────────────────────────
-- Measured data. SELECT only, same reasoning.

DROP POLICY IF EXISTS employee_day_stat_select ON public.employee_day_stats;
CREATE POLICY employee_day_stat_select ON public.employee_day_stats
  FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()));

-- ── CAMERA LINKS ───────────────────────────────────────────────────────────
-- Topology is configuration, like zones.

DROP POLICY IF EXISTS camera_link_select ON public.camera_links;
CREATE POLICY camera_link_select ON public.camera_links
  FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()));

DROP POLICY IF EXISTS camera_link_insert ON public.camera_links;
CREATE POLICY camera_link_insert ON public.camera_links
  FOR INSERT TO authenticated
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));

DROP POLICY IF EXISTS camera_link_update ON public.camera_links;
CREATE POLICY camera_link_update ON public.camera_links
  FOR UPDATE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()))
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));

DROP POLICY IF EXISTS camera_link_delete ON public.camera_links;
CREATE POLICY camera_link_delete ON public.camera_links
  FOR DELETE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()));

-- ═══════════════════════════════════════════════════════════════════════════
--  GRANTS
-- ═══════════════════════════════════════════════════════════════════════════
--
-- 003's `GRANT ... ON ALL TABLES` was a one-time sweep over the tables that
-- existed then — it is NOT a standing rule, as 003 itself warns. Tables created
-- here therefore need their grants stated explicitly, or every query from the
-- app fails with "permission denied for table employees" no matter how correct
-- the policies are.
--
-- `anon` needs nothing: 004 set ALTER DEFAULT PRIVILEGES to revoke it from
-- future tables, and it is revoked again below to make that explicit rather
-- than inherited.

REVOKE ALL ON public.employees          FROM anon;
REVOKE ALL ON public.face_templates     FROM anon;
REVOKE ALL ON public.identity_events    FROM anon;
REVOKE ALL ON public.employee_day_stats FROM anon;
REVOKE ALL ON public.camera_links       FROM anon;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.employees      TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.camera_links   TO authenticated;

-- Measured data stays read-only for browser clients regardless of policy. This
-- is belt and braces with the missing INSERT/UPDATE/DELETE policies above: a
-- policy added carelessly later still cannot make these writable.
GRANT SELECT ON public.identity_events    TO authenticated;
GRANT SELECT ON public.employee_day_stats TO authenticated;

-- The CV pipeline writes both of these through the service key. Supabase
-- normally provisions service_role's table grants itself, so this is usually a
-- no-op — but BYPASSRLS only bypasses row-level POLICIES, never table-level
-- PRIVILEGES. They are separate mechanisms, and a service_role holding no
-- INSERT privilege gets "permission denied for table identity_events" no
-- matter how many policies it is exempt from. Measured on a plain Postgres 17
-- container, where the pipeline's writes failed for exactly this reason.
-- Stating the grants here makes the schema self-sufficient rather than
-- dependent on how the host provisioned its roles.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.identity_events    TO service_role;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.employee_day_stats TO service_role;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.employees          TO service_role;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.face_templates     TO service_role;
    GRANT SELECT, INSERT, UPDATE, DELETE ON public.camera_links       TO service_role;
  END IF;
END $$;

-- THE BIOMETRIC COLUMN — same treatment 004 gives the RTSP credential, and for
-- the same reason: the API surface must not be able to exfiltrate a secret
-- merely because the row is visible.
--
-- CAREFUL, and verified the hard way: a table-level `GRANT SELECT ON
-- face_templates` silently supersedes a column-level REVOKE. Postgres treats
-- the table grant as covering every column, so `REVOKE SELECT ("embedding")`
-- reports success and changes nothing — measured on Postgres 17.11, where
-- `authenticated` could still read every vector afterwards. 004 documents this
-- same trap for `cameras.rtspUrl`.
--
-- The only construction that actually holds: hold NO table-level SELECT, and
-- grant each permitted column explicitly.
REVOKE SELECT ON public.face_templates FROM authenticated;

GRANT SELECT (
  id, "employeeId", quality, "createdAt"
  -- "embedding" deliberately absent. The enrolment UI needs to know a template
  -- EXISTS and how good it is, never what it contains. Only the service role
  -- (the CV backend, which bypasses RLS) reads the vectors it matches against.
) ON public.face_templates TO authenticated;

-- Writes still need the column: enrolment must be able to store a vector it
-- can never read back. Write-without-read is exactly the asymmetry we want.
GRANT INSERT, DELETE ON public.face_templates TO authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
--  SOFT DELETE
-- ═══════════════════════════════════════════════════════════════════════════
--
-- A plain `UPDATE employees SET "deletedAt" = NOW()` is REJECTED by RLS, and
-- this is not a mistake in the policies above — it is inherent to the shape.
-- Measured on Postgres 17.11, narrowed by elimination: updating `displayName`
-- succeeds, updating `active` succeeds, updating `deletedAt` alone fails with
-- "new row violates row-level security policy". `employee_select` filters
-- `deletedAt IS NULL`, and an UPDATE must leave the row still visible to the
-- SELECT policy — so a row that makes itself invisible cannot be written by
-- the session that owns it. Widening the WITH CHECK does not help; the SELECT
-- policy is what bites.
--
-- `zones`, `cameras` and `organisations` all share this property, verified in
-- the same session. `organisations` already solved it exactly this way in 013:
-- a SECURITY DEFINER function is the only door. So deletion here takes the
-- same shape, and the function does its own authorisation rather than
-- inheriting any — SECURITY DEFINER bypasses RLS, so the check must be
-- explicit or this becomes a hole rather than a door.
CREATE OR REPLACE FUNCTION public.soft_delete_employee(p_employee_id UUID)
RETURNS TABLE (id UUID, "displayName" VARCHAR)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  v_org_id UUID;
BEGIN
  -- Read the owning org from inside the definer context, where the row is
  -- visible regardless of the caller's policies.
  SELECT e."orgId" INTO v_org_id
  FROM public.employees e
  WHERE e.id = p_employee_id AND e."deletedAt" IS NULL;

  IF v_org_id IS NULL THEN
    RAISE EXCEPTION 'Employee not found.' USING ERRCODE = 'no_data_found';
  END IF;

  -- THE AUTHORISATION. Mirrors `manage_org_ids()` — ADMIN or MANAGER with an
  -- ACTIVE membership in the owning org. Without this, any authenticated user
  -- who guessed a UUID could delete anyone's employee record.
  IF NOT EXISTS (
    SELECT 1 FROM public.memberships m
    WHERE m."orgId" = v_org_id
      AND m."profileId" = (SELECT auth.uid())
      AND m.status = 'ACTIVE'
      AND m.role IN ('ADMIN', 'MANAGER')
  ) THEN
    RAISE EXCEPTION 'Only an administrator or manager can remove an employee.'
      USING ERRCODE = 'insufficient_privilege';
  END IF;

  RETURN QUERY
  UPDATE public.employees e
     SET "deletedAt" = NOW(),
         -- Release the desk so it can be reassigned immediately. The partial
         -- unique index already ignores deleted rows, but clearing the column
         -- makes the intent legible in the data rather than implied by an index.
         "assignedZoneId" = NULL,
         active = FALSE
   WHERE e.id = p_employee_id
  RETURNING e.id, e."displayName";
END;
$$;

REVOKE ALL ON FUNCTION public.soft_delete_employee(UUID) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.soft_delete_employee(UUID) TO authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
--  RETENTION
-- ═══════════════════════════════════════════════════════════════════════════
--
-- identity_events is per-observation and identifies named people, so it has
-- the shortest retention in the schema. The durable record is the daily
-- rollup, which is aggregate.
--
-- Registered on the same pg_cron schedule pattern as 011. Guarded so this file
-- still applies cleanly on a database without pg_cron (a plain Postgres
-- container, or a local dev instance) — the function is created either way and
-- can be called manually.

CREATE OR REPLACE FUNCTION public.purge_identity_events(retain_days INTEGER DEFAULT 7)
RETURNS INTEGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  removed INTEGER;
BEGIN
  DELETE FROM public.identity_events
  WHERE "observedAt" < NOW() - (retain_days || ' days')::INTERVAL;
  GET DIAGNOSTICS removed = ROW_COUNT;
  RETURN removed;
END;
$$;

-- Only the scheduler and the backend may purge. An ordinary session calling
-- this would be deleting measured data through a SECURITY DEFINER function,
-- which is precisely the hole such functions are known for.
--
-- Revoking from PUBLIC alone is not enough to make it callable by the right
-- caller: the grant to service_role has to be stated, or the retention job
-- fails with "permission denied for function purge_identity_events" — measured.
-- 019 grants purge_deleted_organisations() the same way.
REVOKE ALL ON FUNCTION public.purge_identity_events(INTEGER) FROM PUBLIC, anon, authenticated;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    GRANT EXECUTE ON FUNCTION public.purge_identity_events(INTEGER) TO service_role;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    PERFORM cron.unschedule('purge-identity-events')
      WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'purge-identity-events');
    -- 03:05 UTC: before the 03:15 telemetry purge in 011, so the two do not
    -- contend for the same autovacuum window.
    PERFORM cron.schedule(
      'purge-identity-events', '5 3 * * *',
      $cron$SELECT public.purge_identity_events(7);$cron$
    );
  END IF;
END $$;
