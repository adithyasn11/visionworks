-- VisionWorks — safety summary for the overview and reports.
--
-- ONE function, run as the CALLER (not SECURITY DEFINER), so Row Level Security scopes it exactly as it
-- scopes a direct query: a user sees counts of their own organisation's decisions and nothing else.
-- Aggregating in Postgres keeps the answer exact (PostgREST caps a plain select at 1000 rows without
-- an error, which would make a count silently wrong) and the payload small.
--
--   by_zone  the 50 zones with the most flags (ties: most decisions, then name)
--   by_day   one entry for EVERY day of the window in the caller's time zone (p_tz, an IANA name such as
--            'Asia/Kolkata'), zero-filled, so a chart has no gaps and a late-evening incident lands on the
--            site's own day; the first day is partial when the window starts mid-day
--
-- Idempotent. The first version had three arguments; it is dropped so the new (p_tz defaulted) signature
-- is the only one and a call with three arguments is not ambiguous.

DROP FUNCTION IF EXISTS public.safety_summary(UUID, TIMESTAMPTZ, TIMESTAMPTZ);

CREATE OR REPLACE FUNCTION public.safety_summary(
  p_org UUID, p_since TIMESTAMPTZ, p_until TIMESTAMPTZ, p_tz TEXT DEFAULT 'UTC')
RETURNS JSONB
LANGUAGE plpgsql STABLE SET search_path = ''
AS $$
DECLARE v JSONB;
BEGIN
  IF p_since IS NULL OR p_until IS NULL OR p_until <= p_since THEN
    RAISE EXCEPTION 'the window must have a start before its end';
  END IF;
  IF p_until - p_since > INTERVAL '366 days' THEN
    RAISE EXCEPTION 'the window is limited to 366 days';
  END IF;
  IF p_tz IS NULL OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_timezone_names n WHERE n.name = p_tz) THEN
    RAISE EXCEPTION 'unknown time zone';
  END IF;

  SELECT jsonb_build_object(
    'since', p_since, 'until', p_until, 'time_zone', p_tz,
    'total', count(*),
    'compliant', count(*) FILTER (WHERE d.status = 'COMPLIANT'),
    'potential_violations', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION'),
    'abstentions', count(*) FILTER (WHERE d.status = 'UNVERIFIABLE'),
    'open_alerts', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'PENDING'),
    'confirmed', count(*) FILTER (WHERE d."reviewState" = 'CONFIRMED'),
    'dismissed', count(*) FILTER (WHERE d."reviewState" = 'DISMISSED'),
    -- of the flags a person has judged, how many did they agree with
    'confirmed_share_of_reviewed',
      CASE WHEN count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" IN ('CONFIRMED','DISMISSED')) = 0
           THEN NULL
           ELSE round(
                  (count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'CONFIRMED'))::numeric
                  / (count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" IN ('CONFIRMED','DISMISSED')))::numeric,
                  3)
      END
  ) INTO v
  FROM public.safety_decisions d
  WHERE d."orgId" = p_org AND d."observedAt" >= p_since AND d."observedAt" < p_until;

  v := v || jsonb_build_object(
    'by_zone', COALESCE((
      SELECT jsonb_agg(s.z ORDER BY s.flags DESC, s.decisions DESC, s.zone_name)
        FROM (
          SELECT jsonb_build_object(
                   'zone_id', d."zoneId",
                   -- no zone, or a zone this caller cannot see (deleted): say which
                   'zone', CASE WHEN d."zoneId" IS NULL THEN 'No zone' ELSE COALESCE(zn.name, '(zone no longer available)') END,
                   'decisions', count(*),
                   'potential_violations', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION'),
                   'open_alerts', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'PENDING')) AS z,
                 count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION') AS flags,
                 count(*) AS decisions,
                 COALESCE(zn.name, '') AS zone_name
            FROM public.safety_decisions d
            LEFT JOIN public.zones zn ON zn.id = d."zoneId"
           WHERE d."orgId" = p_org AND d."observedAt" >= p_since AND d."observedAt" < p_until
           GROUP BY d."zoneId", zn.name
           ORDER BY flags DESC, decisions DESC, zone_name
           LIMIT 50) s), '[]'::jsonb),
    'by_day', COALESCE((
      SELECT jsonb_agg(jsonb_build_object('day', to_char(g.day, 'YYYY-MM-DD'),
                                          'decisions', COALESCE(c.decisions, 0),
                                          'potential_violations', COALESCE(c.flags, 0))
                       ORDER BY g.day)
        FROM generate_series((p_since AT TIME ZONE p_tz)::date, ((p_until - INTERVAL '1 microsecond') AT TIME ZONE p_tz)::date, INTERVAL '1 day') AS g(day)
        LEFT JOIN (
          SELECT (d."observedAt" AT TIME ZONE p_tz)::date AS day, count(*) AS decisions,
                 count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION') AS flags
            FROM public.safety_decisions d
           WHERE d."orgId" = p_org AND d."observedAt" >= p_since AND d."observedAt" < p_until
           GROUP BY 1) c ON c.day = g.day::date), '[]'::jsonb));
  RETURN v;
END;
$$;
REVOKE ALL ON FUNCTION public.safety_summary(UUID, TIMESTAMPTZ, TIMESTAMPTZ, TEXT) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.safety_summary(UUID, TIMESTAMPTZ, TIMESTAMPTZ, TEXT) TO authenticated;

NOTIFY pgrst, 'reload schema';
