-- VisionWorks — restrict alert acknowledgement to ADMIN + MANAGER
--
-- THE GAP THIS CLOSES
--
-- `alert_update` was written as:
--
--   USING      ("orgId" IN (SELECT user_org_ids()))
--   WITH CHECK ("orgId" IN (SELECT user_org_ids()))
--
-- `user_org_ids()` is membership-based and covers ALL THREE roles, so a VIEWER
-- could acknowledge and resolve alerts. Measured during Step 7 verification: a
-- VIEWER's UPDATE on an OPEN alert returned `1 row updated`.
--
-- That is not what a VIEWER is. Every other write in the product uses
-- `manage_org_ids()` (ADMIN + MANAGER) for operational acts — zones, cameras,
-- sites, running an analysis — and acknowledging an alert is an operational act
-- in exactly the same sense: it says "seen, being handled", and it RE-ARMS the
-- rule, because the engine skips firing while an alert is still OPEN for that
-- zone. A read-only role silently re-arming an alerting rule is a real effect,
-- not a cosmetic one.
--
-- The application already refused this (`acknowledgeAlert` requires
-- `analysis.run`), so behaviour does not change for anyone using the UI. What
-- changes is that the app check stops being the ONLY thing standing there —
-- which is the layering every other table in this schema already has, and the
-- lesson Step 4 was built on.
--
-- READING IS DELIBERATELY LEFT ALONE
--
-- `alert_select` still uses `user_org_ids()`. Knowing the space is overcrowded
-- is not a privileged fact, and hiding alerts from a VIEWER would hide
-- something the dashboard already shows them the underlying numbers for.
--
-- Apply AFTER 009_dashboard_analytics.sql.

DROP POLICY IF EXISTS alert_update ON public.alerts;

CREATE POLICY alert_update ON public.alerts
  FOR UPDATE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()))
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));
