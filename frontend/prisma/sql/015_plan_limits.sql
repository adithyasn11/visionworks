-- VisionWorks — ENFORCE the plan limits
--
-- ═══════════════════════════════════════════════════════════════════════════
--  WHY THIS FILE EXISTS
-- ═══════════════════════════════════════════════════════════════════════════
--
-- 014_plans.sql recorded a tier. It did not enforce one. The pricing table
-- said "1 camera" and the product happily accepted ten — the limits lived only
-- in `app/lib/plans.js`, where they were rendered as marketing copy and never
-- consulted by a write path.
--
-- A limit that is only displayed is not a limit. This file makes the database
-- the authority for the three that are countable, so a direct PostgREST call
-- with an anon key is refused exactly like a click in the UI.
--
-- ═══════════════════════════════════════════════════════════════════════════
--  WHY THE NUMBERS ARE DUPLICATED IN plans.js AND HERE
-- ═══════════════════════════════════════════════════════════════════════════
--
-- They are not duplicated for convenience. `plans.js` needs them to RENDER the
-- pricing cards; Postgres needs them to ENFORCE. Neither can read the other:
-- a browser cannot import a Postgres function, and a trigger cannot import a
-- JS module.
--
-- The duplication is therefore accepted and made loud instead of hidden:
--   * this table is the AUTHORITY — if the two disagree, this one wins,
--     because this one is what actually refuses the insert
--   * `plans.js` carries a comment pointing here
--   * scripts/check-plan-limits.mjs compares them and fails if they drift
--
-- A single source would mean generating one from the other at build time,
-- which trades a checkable duplication for an invisible build step.
--
-- ═══════════════════════════════════════════════════════════════════════════
--  WHAT IS AND IS NOT ENFORCED
-- ═══════════════════════════════════════════════════════════════════════════
--
--   cameras   ENFORCED  countable rows, trigger below
--   sites     ENFORCED  countable rows, trigger below
--   seats     ENFORCED  countable memberships, trigger below
--   history   CAPPED    `dataRetentionDays` is clamped on write (see below).
--                       It is NOT enforced retroactively, and that is
--                       deliberate: shortening retention DELETES measurements
--                       the next time the nightly job runs. Silently dropping
--                       an org's history because their tier says 7 days would
--                       destroy real data as a side effect of a pricing rule.
--                       The cap stops them SETTING a longer window; it never
--                       shortens one on its own.
--
-- Feature flags (scheduled reports, alert rules) are not enforced here — they
-- are not row counts, and gating them belongs in the actions that build them.
--
-- Apply AFTER 014_plans.sql.

-- ═══════════════════════════════════════════════════════════════════════════
--  THE LIMIT TABLE
-- ═══════════════════════════════════════════════════════════════════════════
--
-- A table rather than a CASE inside each trigger: the values are data, they
-- are read by three triggers plus the check script, and a table can be
-- inspected by anyone debugging a refusal.
--
-- NULL means UNLIMITED, never "none". Zero would be a real limit of zero, and
-- conflating the two is how an Enterprise org ends up unable to add a camera.
CREATE TABLE IF NOT EXISTS public.plan_limits (
  plan            "PlanTier" PRIMARY KEY,
  max_cameras     INT,
  max_sites       INT,
  max_seats       INT,
  max_retention_days INT NOT NULL
);

-- Idempotent: re-running this file updates the numbers rather than failing.
INSERT INTO public.plan_limits (plan, max_cameras, max_sites, max_seats, max_retention_days)
VALUES
  ('FREE',        1,    1,    3,    7),
  ('GROWTH',      10,   5,    25,   90),
  ('ENTERPRISE',  NULL, NULL, NULL, 365)
ON CONFLICT (plan) DO UPDATE SET
  max_cameras        = EXCLUDED.max_cameras,
  max_sites          = EXCLUDED.max_sites,
  max_seats          = EXCLUDED.max_seats,
  max_retention_days = EXCLUDED.max_retention_days;

COMMENT ON TABLE public.plan_limits IS
  'AUTHORITY for plan limits. Mirrored for display in app/lib/plans.js; scripts/check-plan-limits.mjs fails the build if they drift. NULL = unlimited, never zero.';

-- Readable by any signed-in user so the UI can explain a refusal ("Starter
-- allows 1 camera") without hardcoding the number a second time. There is no
-- write policy: only the service role and migrations change these.
ALTER TABLE public.plan_limits ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS plan_limits_select ON public.plan_limits;
CREATE POLICY plan_limits_select ON public.plan_limits
  FOR SELECT TO authenticated
  USING (true);

GRANT SELECT ON public.plan_limits TO authenticated;

-- ═══════════════════════════════════════════════════════════════════════════
--  THE ENFORCEMENT TRIGGERS
-- ═══════════════════════════════════════════════════════════════════════════
--
-- SECURITY DEFINER, because the trigger must count rows the CALLER cannot
-- necessarily see. A VIEWER inserting nothing is irrelevant, but a member of
-- org A must not be able to probe org B's camera count — and without DEFINER
-- the count would be RLS-filtered to whatever the caller can read, which would
-- UNDERCOUNT and let the limit be bypassed by anyone whose view is partial.
--
-- Counting is done with a plain COUNT rather than a cached column. A counter
-- column would be faster and would drift; these tables hold tens of rows per
-- org, not millions.
--
-- RACE CONDITIONS: two simultaneous inserts can both see "0 cameras" and both
-- succeed, landing one row over the cap. That is accepted here rather than
-- taking a table lock on every insert: the overshoot is at most one row, it
-- requires genuinely concurrent requests from the same org, and the cost of
-- the alternative — serialising all camera creation per org — is worse than
-- the failure it prevents. A hard guarantee would need the limit as a real
-- constraint over a counter column, which is a bigger change than the problem
-- justifies.

CREATE OR REPLACE FUNCTION public.enforce_camera_limit()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_plan  "PlanTier";
  v_max   INT;
  v_count INT;
BEGIN
  SELECT plan INTO v_plan FROM public.organisations WHERE id = NEW."orgId";
  IF v_plan IS NULL THEN
    -- No organisation: the FK will reject this anyway. Let it, rather than
    -- raising a confusing "plan limit" error for a missing parent.
    RETURN NEW;
  END IF;

  SELECT max_cameras INTO v_max FROM public.plan_limits WHERE plan = v_plan;

  -- NULL = unlimited. Checked explicitly: `v_count >= NULL` is NULL, not
  -- false, so an IF on it would silently never fire and the branch would look
  -- like it worked.
  IF v_max IS NULL THEN
    RETURN NEW;
  END IF;

  SELECT count(*) INTO v_count FROM public.cameras WHERE "orgId" = NEW."orgId";

  IF v_count >= v_max THEN
    RAISE EXCEPTION
      'plan_limit_cameras: the % plan allows % camera(s); this organisation already has %.',
      v_plan, v_max, v_count
      USING ERRCODE = 'check_violation';
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS camera_plan_limit ON public.cameras;
CREATE TRIGGER camera_plan_limit
  BEFORE INSERT ON public.cameras
  FOR EACH ROW EXECUTE FUNCTION public.enforce_camera_limit();


CREATE OR REPLACE FUNCTION public.enforce_site_limit()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_plan  "PlanTier";
  v_max   INT;
  v_count INT;
BEGIN
  SELECT plan INTO v_plan FROM public.organisations WHERE id = NEW."orgId";
  IF v_plan IS NULL THEN
    RETURN NEW;
  END IF;

  SELECT max_sites INTO v_max FROM public.plan_limits WHERE plan = v_plan;
  IF v_max IS NULL THEN
    RETURN NEW;
  END IF;

  SELECT count(*) INTO v_count FROM public.sites WHERE "orgId" = NEW."orgId";

  IF v_count >= v_max THEN
    RAISE EXCEPTION
      'plan_limit_sites: the % plan allows % site(s); this organisation already has %.',
      v_plan, v_max, v_count
      USING ERRCODE = 'check_violation';
  END IF;

  RETURN NEW;
END;
$$;

-- NOTE ON create_organisation(): it inserts the organisation FIRST, then the
-- first site. By the time this trigger runs the org exists with count 0, so
-- the first site is always allowed even on FREE (max_sites = 1). Verified
-- rather than assumed — see the test in the apply script.
DROP TRIGGER IF EXISTS site_plan_limit ON public.sites;
CREATE TRIGGER site_plan_limit
  BEFORE INSERT ON public.sites
  FOR EACH ROW EXECUTE FUNCTION public.enforce_site_limit();


CREATE OR REPLACE FUNCTION public.enforce_seat_limit()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_plan  "PlanTier";
  v_max   INT;
  v_count INT;
BEGIN
  SELECT plan INTO v_plan FROM public.organisations WHERE id = NEW."orgId";
  IF v_plan IS NULL THEN
    RETURN NEW;
  END IF;

  SELECT max_seats INTO v_max FROM public.plan_limits WHERE plan = v_plan;
  IF v_max IS NULL THEN
    RETURN NEW;
  END IF;

  -- REVOKED memberships do not consume a seat — they are history, not people.
  -- INVITED does: an outstanding invitation is a seat being held open, and not
  -- counting it would let an admin invite twenty people onto a three-seat plan
  -- and only discover the problem as they accepted.
  SELECT count(*) INTO v_count
    FROM public.memberships
   WHERE "orgId" = NEW."orgId"
     AND status IN ('ACTIVE', 'INVITED');

  IF v_count >= v_max THEN
    RAISE EXCEPTION
      'plan_limit_seats: the % plan allows % member(s); this organisation already has % (including pending invitations).',
      v_plan, v_max, v_count
      USING ERRCODE = 'check_violation';
  END IF;

  RETURN NEW;
END;
$$;

-- INSERT only, never UPDATE. An UPDATE trigger would fire when a member
-- ACCEPTS an invitation (INVITED -> ACTIVE), and since the invite already
-- consumed a seat the count would include the row being updated and refuse the
-- acceptance — locking out the exact person who was legitimately invited.
DROP TRIGGER IF EXISTS seat_plan_limit ON public.memberships;
CREATE TRIGGER seat_plan_limit
  BEFORE INSERT ON public.memberships
  FOR EACH ROW EXECUTE FUNCTION public.enforce_seat_limit();

-- ═══════════════════════════════════════════════════════════════════════════
--  RETENTION CAP
-- ═══════════════════════════════════════════════════════════════════════════
--
-- CLAMPS rather than refuses, and only on the way UP.
--
-- Refusing would be the obvious choice and it is the wrong one here: this
-- column already exists on every organisation, and a plan downgrade would make
-- every subsequent settings save fail on a field the admin did not touch.
-- Clamping means "you may not ask for more than your tier allows", which is the
-- actual rule.
--
-- It never SHORTENS an existing value. `dataRetentionDays` is the input to the
-- nightly deletion job — lowering it destroys measurements. A pricing rule must
-- not delete data as a side effect; that stays an explicit admin action with
-- the counted-impact warning the settings screen already shows.
CREATE OR REPLACE FUNCTION public.clamp_retention_to_plan()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_max INT;
BEGIN
  SELECT max_retention_days INTO v_max FROM public.plan_limits WHERE plan = NEW.plan;
  IF v_max IS NULL THEN
    RETURN NEW;
  END IF;

  -- Only clamp an INCREASE beyond the cap. On UPDATE, a value that was already
  -- above the cap (a downgrade) is left exactly as it was.
  IF NEW."dataRetentionDays" > v_max THEN
    IF TG_OP = 'UPDATE' AND OLD."dataRetentionDays" > v_max
       AND NEW."dataRetentionDays" <= OLD."dataRetentionDays" THEN
      -- Grandfathered value, not being raised. Leave it.
      RETURN NEW;
    END IF;
    NEW."dataRetentionDays" := v_max;
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS org_retention_plan_cap ON public.organisations;
CREATE TRIGGER org_retention_plan_cap
  BEFORE INSERT OR UPDATE OF "dataRetentionDays", plan ON public.organisations
  FOR EACH ROW EXECUTE FUNCTION public.clamp_retention_to_plan();

-- ═══════════════════════════════════════════════════════════════════════════
--  A READ HELPER FOR THE UI
-- ═══════════════════════════════════════════════════════════════════════════
--
-- "How much of my allowance am I using" in one round trip, so the Plan panel
-- shows 2/10 cameras rather than a bare limit the reader has to compare by
-- hand.
--
-- NOT SECURITY DEFINER. It runs as the caller, so `user_org_ids()` filters it
-- and a member can only ever see their own organisation's usage. The counts
-- inside are RLS-filtered too, which is correct here: this is a display of
-- what the reader can see, not an enforcement decision.
CREATE OR REPLACE FUNCTION public.plan_usage(p_org_id UUID)
RETURNS TABLE (
  plan "PlanTier",
  cameras_used INT, cameras_max INT,
  sites_used INT, sites_max INT,
  seats_used INT, seats_max INT,
  retention_days INT, retention_max INT
)
LANGUAGE sql
STABLE
SET search_path = public
AS $$
  SELECT
    o.plan,
    (SELECT count(*)::int FROM public.cameras  c WHERE c."orgId" = o.id),
    l.max_cameras,
    (SELECT count(*)::int FROM public.sites    s WHERE s."orgId" = o.id),
    l.max_sites,
    (SELECT count(*)::int FROM public.memberships m
      WHERE m."orgId" = o.id AND m.status IN ('ACTIVE','INVITED')),
    l.max_seats,
    o."dataRetentionDays",
    l.max_retention_days
  FROM public.organisations o
  JOIN public.plan_limits l ON l.plan = o.plan
  WHERE o.id = p_org_id
    AND o."deletedAt" IS NULL;
$$;

REVOKE ALL ON FUNCTION public.plan_usage(UUID) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.plan_usage(UUID) TO authenticated;
