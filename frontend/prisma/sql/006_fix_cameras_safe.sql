-- VisionWorks — fix cameras_safe
--
-- THE BUG
--
-- Every `select … from public.cameras_safe` as `authenticated` failed with
--
--     42501: permission denied for table cameras
--
-- so the view has been unusable since 004 created it. Two changes in that file
-- are individually correct and jointly impossible:
--
--   1. `security_invoker = true` on the view, so it runs with the CALLER's
--      privileges and honours RLS rather than becoming a hole through it.
--
--   2. `REVOKE SELECT ON public.cameras FROM authenticated`, replaced with
--      column-level grants that omit "rtspUrl", so a browser client can never
--      read a camera password.
--
-- The conflict is a Postgres rule that is easy to miss: reading a view requires
-- **table-level** SELECT on its base table. Column-level grants are checked for
-- direct column access but do not satisfy the view's own permission check. With
-- table SELECT revoked, a security_invoker view over `cameras` can never work
-- for `authenticated`, no matter which columns it selects.
--
-- THE FIX, AND WHY THIS SHAPE
--
-- Flip the view to `security_definer` (the Postgres default) so it reads
-- `cameras` as its owner — and then re-implement the tenant filter *inside* the
-- view, because a definer view does NOT apply the caller's RLS policies.
--
-- That last point is the whole risk of this approach: a definer view over a
-- tenant table is exactly how people accidentally expose every customer's rows.
-- So the WHERE clause below reproduces the policy set from 003/005 explicitly:
--
--     org member  (user_org_ids)      -> their own org's cameras
--     platform op (is_platform_admin) -> all cameras, metadata only
--
-- Both helpers are STABLE and set-returning/boolean over `memberships`, so this
-- is the same predicate the RLS policies use, evaluated once per query.
--
-- `rtspUrl` is still never selected here, and the column-level revoke on
-- `cameras` stays in force — so the credential remains unreadable both directly
-- and through the view. `hasRtspUrl` carries the only fact a caller needs:
-- whether a stream URL is configured.
--
-- Apply AFTER 005_platform_admin.sql.

DROP VIEW IF EXISTS public.cameras_safe;

CREATE VIEW public.cameras_safe AS
SELECT
  c.id,
  c."orgId",
  c."siteId",
  c.name,
  c.description,
  c."sourceType",
  -- Presence, never the value.
  (c."rtspUrl" IS NOT NULL) AS "hasRtspUrl",
  c."deviceIndex",
  c."fpsTarget",
  c."frameWidth",
  c."frameHeight",
  (c."homographyMatrix" IS NOT NULL) AS "isCalibrated",
  c.status,
  c."lastSeenAt",
  c."lastErrorMessage",
  c."createdAt",
  c."updatedAt"
FROM public.cameras c
WHERE c."deletedAt" IS NULL
  -- The tenant boundary, restated because a definer view bypasses RLS.
  AND (
    c."orgId" IN (SELECT public.user_org_ids())
    OR (SELECT public.is_platform_admin())
  );

COMMENT ON VIEW public.cameras_safe IS
  'Cameras without the rtspUrl credential. SECURITY DEFINER by necessity '
  '(table-level SELECT on cameras is revoked from authenticated), so the tenant '
  'filter is enforced in the view body rather than by RLS. Do not add a column '
  'that reads "rtspUrl", and do not remove the orgId predicate.';

-- Supabase grants ALL on a newly created object to anon and authenticated, so
-- the revoke must follow every CREATE — the ordering trap that left this view
-- readable by anon after 004.
REVOKE ALL    ON public.cameras_safe FROM anon;
GRANT  SELECT ON public.cameras_safe TO authenticated;
