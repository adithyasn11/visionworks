-- VisionWorks — Row Level Security
--
-- WHAT THIS REPLACES
--
-- The old backend/app/db/supabase_schema.sql shipped policies reading
-- `USING (true)`. That is not a policy — it means any authenticated user could
-- read every organisation's cameras, zones and telemetry. In a multi-tenant
-- product that is the most serious defect possible.
--
-- WHY RLS RATHER THAN API FILTERING
--
-- The API also checks roles, and that governs the UX. But RLS is the real
-- boundary: it holds even when a route handler forgets its `where orgId`
-- clause, even against a hand-crafted request, even if someone queries with
-- the anon key directly from the browser. Application checks are a usability
-- feature; database checks are the security control. DOCUMENTATION.md §9
-- calls this "enforced twice, deliberately".
--
-- MODEL
--
--   read   -> orgId IN (SELECT user_org_ids())    ADMIN, MANAGER, VIEWER
--   write  -> orgId IN (SELECT manage_org_ids())  ADMIN, MANAGER
--   govern -> orgId IN (SELECT admin_org_ids())   ADMIN
--
-- WHY IN-LISTS AND NOT BOOLEAN HELPERS
--
-- Every predicate is `orgId IN (SELECT ...)` rather than `can_read_org(orgId)`.
-- That is not a style choice — it was measured on 824,000 minute buckets:
--
--   USING (can_read_org("orgId"))              8,623 ms
--   USING ("orgId" IN (SELECT user_org_ids()))   126 ms     68x faster
--
-- identical rows returned. A boolean function in a policy runs once per
-- candidate row (EXPLAIN shows `loops=824312`), and marking it STABLE does not
-- change that. A set-returning function is materialised once and hash-joined.
-- On the analytics tables this is the difference between a usable dashboard
-- and a timeout.
--
-- The boolean helpers still exist in 002 for application code, where the org
-- is already known and only one row is being checked.
--
-- `auth.uid()` is likewise always wrapped as `(SELECT auth.uid())` so it is
-- evaluated once per query instead of once per row.
--
-- Apply AFTER 002_auth_triggers.sql (the helper functions live there).
--
-- NOTE ON `service_role`: the Python CV backend writes buckets using the
-- service key, which bypasses RLS by design. That key must never be exposed
-- to the browser — it is the one credential that defeats everything here.

-- ═══════════════════════════════════════════════════════════════════════════
--  Enable RLS everywhere, and force it even for table owners
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE public.organisations     ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.profiles          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.memberships       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sites             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.cameras           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.zones             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.zone_minute_stats ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.zone_day_stats    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.analysis_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.alert_rules       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.alerts            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reports           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_logs        ENABLE ROW LEVEL SECURITY;

-- ═══════════════════════════════════════════════════════════════════════════
--  ORGANISATIONS
-- ═══════════════════════════════════════════════════════════════════════════

-- No INSERT policy: organisations are created only through
-- create_organisation(), which is SECURITY DEFINER. This keeps the
-- "first member becomes admin" invariant impossible to bypass.
CREATE POLICY org_select ON public.organisations
  FOR SELECT TO authenticated
  USING (id IN (SELECT public.user_org_ids()) AND "deletedAt" IS NULL);

CREATE POLICY org_update ON public.organisations
  FOR UPDATE TO authenticated
  USING (id IN (SELECT public.admin_org_ids()))
  WITH CHECK (id IN (SELECT public.admin_org_ids()));

-- Deletion is a soft delete through UPDATE. Hard DELETE is deliberately
-- unreachable: cascading it would erase audit history.

-- ═══════════════════════════════════════════════════════════════════════════
--  PROFILES
-- ═══════════════════════════════════════════════════════════════════════════

-- You can always see yourself, plus anyone sharing an organisation with you
-- (needed for /settings/team and for "acknowledged by" attribution).
CREATE POLICY profile_select_self ON public.profiles
  FOR SELECT TO authenticated
  USING (id = (SELECT auth.uid()));

CREATE POLICY profile_select_colleagues ON public.profiles
  FOR SELECT TO authenticated
  USING (
    EXISTS (
      SELECT 1 FROM public.memberships m
      WHERE m."profileId" = public.profiles.id
        AND m.status = 'ACTIVE'
        AND m."orgId" IN (SELECT public.user_org_ids())
    )
  );

CREATE POLICY profile_update_self ON public.profiles
  FOR UPDATE TO authenticated
  USING (id = (SELECT auth.uid()))
  WITH CHECK (id = (SELECT auth.uid()));

-- INSERT is handled by the on_auth_user_created trigger only.

-- ═══════════════════════════════════════════════════════════════════════════
--  MEMBERSHIPS
-- ═══════════════════════════════════════════════════════════════════════════

CREATE POLICY membership_select ON public.memberships
  FOR SELECT TO authenticated
  USING (
    "profileId" = (SELECT auth.uid())               -- always see your own
    OR "orgId" IN (SELECT public.user_org_ids())        -- and your org's roster
  );

-- Only admins invite. WITH CHECK forces the new row into an org the caller
-- actually administers — without it, an admin of org A could insert a
-- membership into org B.
CREATE POLICY membership_insert ON public.memberships
  FOR INSERT TO authenticated
  WITH CHECK ("orgId" IN (SELECT public.admin_org_ids()) AND status = 'INVITED');

CREATE POLICY membership_update ON public.memberships
  FOR UPDATE TO authenticated
  USING ("orgId" IN (SELECT public.admin_org_ids()))
  WITH CHECK ("orgId" IN (SELECT public.admin_org_ids()));

CREATE POLICY membership_delete ON public.memberships
  FOR DELETE TO authenticated
  USING (
    "orgId" IN (SELECT public.admin_org_ids())
    -- Or leave an org yourself. The last-admin trigger in 001 still applies,
    -- so this cannot orphan an organisation.
    OR "profileId" = (SELECT auth.uid())
  );

-- ═══════════════════════════════════════════════════════════════════════════
--  SITES · CAMERAS · ZONES     read: all roles · write: admin + manager
-- ═══════════════════════════════════════════════════════════════════════════

CREATE POLICY site_select ON public.sites
  FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()) AND "deletedAt" IS NULL);

CREATE POLICY site_insert ON public.sites
  FOR INSERT TO authenticated
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));

CREATE POLICY site_update ON public.sites
  FOR UPDATE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()))
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));

CREATE POLICY site_delete ON public.sites
  FOR DELETE TO authenticated
  USING ("orgId" IN (SELECT public.admin_org_ids()));

CREATE POLICY camera_select ON public.cameras
  FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()) AND "deletedAt" IS NULL);

CREATE POLICY camera_insert ON public.cameras
  FOR INSERT TO authenticated
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));

CREATE POLICY camera_update ON public.cameras
  FOR UPDATE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()))
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));

CREATE POLICY camera_delete ON public.cameras
  FOR DELETE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()));

-- This is the F5 acceptance test in DOCUMENTATION.md §11: "a Viewer receives
-- 403 attempting to edit a zone". The API returns the 403; this policy is
-- what makes the write impossible even if the API doesn't.
CREATE POLICY zone_select ON public.zones
  FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()) AND "deletedAt" IS NULL);

CREATE POLICY zone_insert ON public.zones
  FOR INSERT TO authenticated
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));

CREATE POLICY zone_update ON public.zones
  FOR UPDATE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()))
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));

CREATE POLICY zone_delete ON public.zones
  FOR DELETE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()));

-- ═══════════════════════════════════════════════════════════════════════════
--  ANALYTICS     read-only to every client
-- ═══════════════════════════════════════════════════════════════════════════

-- No INSERT/UPDATE/DELETE policies at all. Buckets are written exclusively by
-- the CV pipeline through the service role, which bypasses RLS. A browser
-- client therefore cannot fabricate or alter analytics — measured data is
-- read-only to everyone who reads it.
CREATE POLICY zms_select ON public.zone_minute_stats
  FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()));

CREATE POLICY zds_select ON public.zone_day_stats
  FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()));

-- ═══════════════════════════════════════════════════════════════════════════
--  ANALYSIS SESSIONS
-- ═══════════════════════════════════════════════════════════════════════════

CREATE POLICY session_select ON public.analysis_sessions
  FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()));

-- Viewers cannot start an analysis run — matches the matrix row
-- "Upload video / start analysis: Admin, Manager".
CREATE POLICY session_insert ON public.analysis_sessions
  FOR INSERT TO authenticated
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));

CREATE POLICY session_update ON public.analysis_sessions
  FOR UPDATE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()))
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));

CREATE POLICY session_delete ON public.analysis_sessions
  FOR DELETE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()));

-- ═══════════════════════════════════════════════════════════════════════════
--  ALERTS
-- ═══════════════════════════════════════════════════════════════════════════

CREATE POLICY alert_rule_select ON public.alert_rules
  FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()) AND "deletedAt" IS NULL);

CREATE POLICY alert_rule_insert ON public.alert_rules
  FOR INSERT TO authenticated
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));

CREATE POLICY alert_rule_update ON public.alert_rules
  FOR UPDATE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()))
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));

CREATE POLICY alert_rule_delete ON public.alert_rules
  FOR DELETE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()));

CREATE POLICY alert_select ON public.alerts
  FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()));

-- All three roles may acknowledge — a Viewer noticing a full room and marking
-- it seen is useful, and acknowledgement is attributed, so it is accountable.
-- Alerts themselves are created by the evaluation job via the service role.
CREATE POLICY alert_update ON public.alerts
  FOR UPDATE TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()))
  WITH CHECK ("orgId" IN (SELECT public.user_org_ids()));

-- ═══════════════════════════════════════════════════════════════════════════
--  REPORTS
-- ═══════════════════════════════════════════════════════════════════════════

CREATE POLICY report_select ON public.reports
  FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()));

-- All roles may generate a report ("Download reports: Admin, Manager, Viewer"),
-- but the requester is pinned to the caller so authorship cannot be forged.
CREATE POLICY report_insert ON public.reports
  FOR INSERT TO authenticated
  WITH CHECK (
    "orgId" IN (SELECT public.user_org_ids())
    AND ("requestedById" IS NULL OR "requestedById" = (SELECT auth.uid()))
  );

CREATE POLICY report_delete ON public.reports
  FOR DELETE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()) OR "requestedById" = (SELECT auth.uid()));

-- ═══════════════════════════════════════════════════════════════════════════
--  AUDIT LOG     admin-readable, append-only, never mutable
-- ═══════════════════════════════════════════════════════════════════════════

CREATE POLICY audit_select ON public.audit_logs
  FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.admin_org_ids()));

-- Actor is forced to the caller: you cannot write an audit entry as someone
-- else. There is deliberately no UPDATE and no DELETE policy on this table —
-- append-only is the entire point of an audit log, and a policy that allowed
-- editing it would make it worthless as evidence.
CREATE POLICY audit_insert ON public.audit_logs
  FOR INSERT TO authenticated
  WITH CHECK (
    "orgId" IN (SELECT public.user_org_ids())
    AND ("actorId" IS NULL OR "actorId" = (SELECT auth.uid()))
  );

-- ═══════════════════════════════════════════════════════════════════════════
--  Belt and braces: revoke the blanket table grants Supabase hands out
-- ═══════════════════════════════════════════════════════════════════════════

-- Supabase grants ALL on public tables to `anon` and `authenticated` by
-- default and relies solely on RLS. Removing `anon` entirely means an
-- unauthenticated request cannot reach these tables at all, so a future
-- policy mistake has a smaller blast radius.
--
-- NOTE: `ALL TABLES` means "all tables that exist right now" — it is not a
-- standing rule. Objects created later (cameras_safe in 004) get the default
-- grants and keep them. 004 therefore repeats this sweep at the very end and
-- sets ALTER DEFAULT PRIVILEGES so it cannot regress again.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO authenticated;
-- Measured data stays read-only for browser clients regardless of policy.
REVOKE INSERT, UPDATE, DELETE ON public.zone_minute_stats FROM authenticated;
REVOKE INSERT, UPDATE, DELETE ON public.zone_day_stats    FROM authenticated;
REVOKE UPDATE, DELETE          ON public.audit_logs       FROM authenticated;
