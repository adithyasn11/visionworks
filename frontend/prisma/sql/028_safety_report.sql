-- VisionWorks — the safety report (Step 16).
--
-- ONE function, run as the CALLER (not SECURITY DEFINER), so Row Level Security scopes it exactly as it scopes a
-- direct query: a user gets the figures of their own organisation and nothing else. Aggregating in Postgres keeps
-- every number exact (PostgREST caps a plain select at 1000 rows without an error, which would make a count
-- silently wrong) and the payload small. The window is `observedAt` of the decision or event (when it HAPPENED),
-- in the site's own time zone for the hour and day buckets.
--
-- HONEST CATEGORIES. A report must never present a flag nobody has judged as a violation, so the numbers are
-- kept in the words the review workflow uses:
--   flagged_by_system     every POTENTIAL_VIOLATION the system raised (nobody has necessarily looked at it)
--   confirmed / dismissed / pending      what a person did with those flags
--   insufficient_evidence the decisions the system could not make (UNVERIFIABLE), and how many of them a person
--                         later found to be real or not
-- and `compliance_rate` is compliant / (compliant + flagged): of the decisions where the system made a claim.
--
-- Corrective actions are counted by when they were opened (createdAt) and closed (closedAt); "overdue" is as of
-- now, in the site's time zone. Time in restricted zones comes from visits the tracker saw from start to finish
-- (the worker walked out of the zone): visits that began before the video, or ended by the worker being lost or
-- the video stopping, are counted separately and not timed.
--
-- Idempotent.

CREATE OR REPLACE FUNCTION public.safety_report(
  p_org UUID, p_since TIMESTAMPTZ, p_until TIMESTAMPTZ, p_tz TEXT DEFAULT 'UTC')
RETURNS JSONB
LANGUAGE plpgsql STABLE SET search_path = ''
AS $$
DECLARE
  v JSONB;
  v_today DATE;
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
  v_today := (now() AT TIME ZONE p_tz)::date;

  -- ── the headline and the honest categories ────────────────────────────────
  SELECT jsonb_build_object(
    'since', p_since, 'until', p_until, 'time_zone', p_tz, 'generated_at', now(),
    'decisions', count(*),
    'compliant', count(*) FILTER (WHERE d.status = 'COMPLIANT'),
    'flagged_by_system', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION'),
    'insufficient_evidence', count(*) FILTER (WHERE d.status = 'UNVERIFIABLE'),
    'compliance_rate',
      CASE WHEN count(*) FILTER (WHERE d.status IN ('COMPLIANT', 'POTENTIAL_VIOLATION')) = 0 THEN NULL
           ELSE round((count(*) FILTER (WHERE d.status = 'COMPLIANT'))::numeric
                      / (count(*) FILTER (WHERE d.status IN ('COMPLIANT', 'POTENTIAL_VIOLATION')))::numeric, 4) END,
    'unverifiable_rate',
      CASE WHEN count(*) = 0 THEN NULL
           ELSE round((count(*) FILTER (WHERE d.status = 'UNVERIFIABLE'))::numeric / count(*)::numeric, 4) END,
    'confirmed', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'CONFIRMED'),
    'dismissed', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'DISMISSED'),
    'pending', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'PENDING'),
    'insufficient_confirmed', count(*) FILTER (WHERE d.status = 'UNVERIFIABLE' AND d."reviewState" = 'CONFIRMED'),
    'insufficient_dismissed', count(*) FILTER (WHERE d.status = 'UNVERIFIABLE' AND d."reviewState" = 'DISMISSED'),
    'confirmed_share_of_reviewed',
      CASE WHEN count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" IN ('CONFIRMED', 'DISMISSED')) = 0 THEN NULL
           ELSE round((count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'CONFIRMED'))::numeric
                      / (count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" IN ('CONFIRMED', 'DISMISSED')))::numeric, 3) END,
    'events_recorded', (SELECT count(*) FROM public.safety_events e
                         WHERE e."orgId" = p_org AND e."observedAt" >= p_since AND e."observedAt" < p_until)
  ) INTO v
  FROM public.safety_decisions d
  WHERE d."orgId" = p_org AND d."observedAt" >= p_since AND d."observedAt" < p_until;

  -- ── what the reviewers said, by reason ────────────────────────────────────
  v := v || jsonb_build_object('review_reasons', COALESCE((
    SELECT jsonb_agg(jsonb_build_object('state', r.state, 'reason', r.reason, 'count', r.n) ORDER BY r.state, r.n DESC, r.reason)
      FROM (SELECT d."reviewState" AS state, COALESCE(d."reviewReasonCode", 'NONE') AS reason, count(*) AS n
              FROM public.safety_decisions d
             WHERE d."orgId" = p_org AND d."observedAt" >= p_since AND d."observedAt" < p_until
               AND d."reviewState" IN ('CONFIRMED', 'DISMISSED')
             GROUP BY 1, 2) r), '[]'::jsonb));

  -- ── by rule, zone, camera ─────────────────────────────────────────────────
  v := v || jsonb_build_object(
    'by_rule', COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
               'rule_type', s.rule_type, 'decisions', s.decisions, 'compliant', s.compliant, 'flagged', s.flagged,
               'insufficient_evidence', s.insufficient, 'confirmed', s.confirmed, 'dismissed', s.dismissed, 'pending', s.pending)
             ORDER BY s.flagged DESC, s.decisions DESC, s.rule_type)
        FROM (SELECT d."ruleType" AS rule_type, count(*) AS decisions,
                     count(*) FILTER (WHERE d.status = 'COMPLIANT') AS compliant,
                     count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION') AS flagged,
                     count(*) FILTER (WHERE d.status = 'UNVERIFIABLE') AS insufficient,
                     count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'CONFIRMED') AS confirmed,
                     count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'DISMISSED') AS dismissed,
                     count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'PENDING') AS pending
                FROM public.safety_decisions d
               WHERE d."orgId" = p_org AND d."observedAt" >= p_since AND d."observedAt" < p_until
               GROUP BY d."ruleType") s), '[]'::jsonb),
    'by_zone', COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
               'zone', s.zone, 'decisions', s.decisions, 'compliant', s.compliant, 'flagged', s.flagged,
               'insufficient_evidence', s.insufficient, 'confirmed', s.confirmed, 'dismissed', s.dismissed, 'pending', s.pending)
             ORDER BY s.flagged DESC, s.decisions DESC, s.zone)
        FROM (SELECT CASE WHEN d."zoneId" IS NULL THEN 'No zone' ELSE COALESCE(zn.name, '(zone no longer available)') END AS zone,
                     count(*) AS decisions,
                     count(*) FILTER (WHERE d.status = 'COMPLIANT') AS compliant,
                     count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION') AS flagged,
                     count(*) FILTER (WHERE d.status = 'UNVERIFIABLE') AS insufficient,
                     count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'CONFIRMED') AS confirmed,
                     count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'DISMISSED') AS dismissed,
                     count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'PENDING') AS pending
                FROM public.safety_decisions d
                LEFT JOIN public.zones zn ON zn.id = d."zoneId"
               WHERE d."orgId" = p_org AND d."observedAt" >= p_since AND d."observedAt" < p_until
               GROUP BY d."zoneId", zn.name
               ORDER BY count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION') DESC, count(*) DESC
               LIMIT 100) s), '[]'::jsonb),
    'by_camera', COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
               'camera', s.camera, 'decisions', s.decisions, 'compliant', s.compliant, 'flagged', s.flagged,
               'insufficient_evidence', s.insufficient, 'confirmed', s.confirmed, 'dismissed', s.dismissed, 'pending', s.pending)
             ORDER BY s.flagged DESC, s.decisions DESC, s.camera)
        FROM (SELECT COALESCE(c.name, '(camera no longer available)') AS camera,
                     count(*) AS decisions,
                     count(*) FILTER (WHERE d.status = 'COMPLIANT') AS compliant,
                     count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION') AS flagged,
                     count(*) FILTER (WHERE d.status = 'UNVERIFIABLE') AS insufficient,
                     count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'CONFIRMED') AS confirmed,
                     count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'DISMISSED') AS dismissed,
                     count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'PENDING') AS pending
                FROM public.safety_decisions d
                LEFT JOIN public.cameras c ON c.id = d."cameraId"
               WHERE d."orgId" = p_org AND d."observedAt" >= p_since AND d."observedAt" < p_until
               GROUP BY d."cameraId", c.name
               ORDER BY count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION') DESC, count(*) DESC
               LIMIT 100) s), '[]'::jsonb));

  -- ── by hour of the day and by day, in the site's own time zone ────────────
  v := v || jsonb_build_object(
    'by_hour', COALESCE((
      SELECT jsonb_agg(jsonb_build_object('hour', h.hour, 'decisions', COALESCE(c.decisions, 0), 'flagged', COALESCE(c.flagged, 0)) ORDER BY h.hour)
        FROM generate_series(0, 23) AS h(hour)
        LEFT JOIN (SELECT extract(hour FROM d."observedAt" AT TIME ZONE p_tz)::int AS hour, count(*) AS decisions,
                          count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION') AS flagged
                     FROM public.safety_decisions d
                    WHERE d."orgId" = p_org AND d."observedAt" >= p_since AND d."observedAt" < p_until
                    GROUP BY 1) c ON c.hour = h.hour), '[]'::jsonb),
    'by_day', COALESCE((
      SELECT jsonb_agg(jsonb_build_object('day', to_char(g.day, 'YYYY-MM-DD'), 'decisions', COALESCE(c.decisions, 0), 'flagged', COALESCE(c.flagged, 0))
                       ORDER BY g.day)
        FROM generate_series((p_since AT TIME ZONE p_tz)::date, ((p_until - INTERVAL '1 microsecond') AT TIME ZONE p_tz)::date, INTERVAL '1 day') AS g(day)
        LEFT JOIN (SELECT (d."observedAt" AT TIME ZONE p_tz)::date AS day, count(*) AS decisions,
                          count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION') AS flagged
                     FROM public.safety_decisions d
                    WHERE d."orgId" = p_org AND d."observedAt" >= p_since AND d."observedAt" < p_until
                    GROUP BY 1) c ON c.day = g.day::date), '[]'::jsonb));

  -- ── helmet and vest: what was found missing, by decision ──────────────────
  v := v || jsonb_build_object('ppe', (
    SELECT jsonb_build_object(
             'flagged', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION'),
             'missing_helmet', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d.evidence #>> '{items,helmet,state}' = 'ABSENT'),
             'missing_vest', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d.evidence #>> '{items,safety_vest,state}' = 'ABSENT'),
             'missing_both', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION'
                                                AND d.evidence #>> '{items,helmet,state}' = 'ABSENT' AND d.evidence #>> '{items,safety_vest,state}' = 'ABSENT'),
             'could_not_verify', count(*) FILTER (WHERE d.status = 'UNVERIFIABLE'),
             'compliant', count(*) FILTER (WHERE d.status = 'COMPLIANT'))
      FROM public.safety_decisions d
     WHERE d."orgId" = p_org AND d."observedAt" >= p_since AND d."observedAt" < p_until
       AND d."ruleType" IN ('PPE_ENTRY', 'PPE_CONTINUOUS')));

  -- ── time inside restricted zones: visits seen from start to finish ────────
  v := v || jsonb_build_object('zone_time', (
    SELECT jsonb_build_object(
             'visits_timed', count(*) FILTER (WHERE t.timed),
             'visits_not_timed', count(*) FILTER (WHERE NOT t.timed),
             'average_seconds', round((avg(t.sec) FILTER (WHERE t.timed))::numeric, 1),
             'max_seconds', round((max(t.sec) FILTER (WHERE t.timed))::numeric, 1),
             'by_zone', COALESCE((
               SELECT jsonb_agg(jsonb_build_object('zone', z.zone, 'visits', z.visits, 'average_seconds', z.avg_s, 'max_seconds', z.max_s)
                                ORDER BY z.visits DESC, z.zone)
                 FROM (SELECT COALESCE(zn.name, '(zone no longer available)') AS zone, count(*) AS visits,
                              round((avg(x.sec))::numeric, 1) AS avg_s, round((max(x.sec))::numeric, 1) AS max_s
                         FROM (SELECT e."zoneId", (e.details ->> 'dwell_s')::float AS sec
                                 FROM public.safety_events e
                                 JOIN public.zones z0 ON z0.id = e."zoneId" AND z0."safetyType" = 'RESTRICTED'
                                WHERE e."orgId" = p_org AND e."observedAt" >= p_since AND e."observedAt" < p_until
                                  AND e."eventType" = 'EXITED_ZONE' AND e.details ->> 'reason' = 'LEFT'
                                  AND jsonb_typeof(e.details -> 'dwell_s') = 'number'
                                  AND COALESCE(e.details ->> 'first_sighting', 'false') <> 'true'
                                  AND COALESCE(e.details ->> 'visit_started_in_replay', 'false') <> 'true') x
                         LEFT JOIN public.zones zn ON zn.id = x."zoneId"
                        GROUP BY x."zoneId", zn.name
                        ORDER BY count(*) DESC LIMIT 50) z), '[]'::jsonb))
      FROM (SELECT COALESCE(e.details ->> 'reason' = 'LEFT' AND jsonb_typeof(e.details -> 'dwell_s') = 'number'
                            AND COALESCE(e.details ->> 'first_sighting', 'false') <> 'true'
                            AND COALESCE(e.details ->> 'visit_started_in_replay', 'false') <> 'true', false) AS timed,
                   CASE WHEN jsonb_typeof(e.details -> 'dwell_s') = 'number' THEN (e.details ->> 'dwell_s')::float END AS sec
              FROM public.safety_events e
              JOIN public.zones z0 ON z0.id = e."zoneId" AND z0."safetyType" = 'RESTRICTED'
             WHERE e."orgId" = p_org AND e."observedAt" >= p_since AND e."observedAt" < p_until
               AND e."eventType" = 'EXITED_ZONE') t));

  -- ── man-down and head-count incidents ─────────────────────────────────────
  v := v || jsonb_build_object(
    'man_down', (
      SELECT jsonb_build_object(
               'flagged', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION'),
               'confirmed', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'CONFIRMED'),
               'dismissed', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'DISMISSED'),
               'pending', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'PENDING'),
               'could_not_verify', count(*) FILTER (WHERE d.status = 'UNVERIFIABLE'))
        FROM public.safety_decisions d
       WHERE d."orgId" = p_org AND d."observedAt" >= p_since AND d."observedAt" < p_until AND d."ruleType" = 'MAN_DOWN'),
    'occupancy', (
      SELECT jsonb_build_object(
               'lone_worker', jsonb_build_object(
                 'flagged', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d.evidence ->> 'outcome' = 'LONE_WORKER'),
                 'confirmed', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d.evidence ->> 'outcome' = 'LONE_WORKER' AND d."reviewState" = 'CONFIRMED'),
                 'dismissed', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d.evidence ->> 'outcome' = 'LONE_WORKER' AND d."reviewState" = 'DISMISSED'),
                 'pending', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d.evidence ->> 'outcome' = 'LONE_WORKER' AND d."reviewState" = 'PENDING')),
               'over_capacity', jsonb_build_object(
                 'flagged', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d.evidence ->> 'outcome' = 'OVER_CAPACITY'),
                 'confirmed', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d.evidence ->> 'outcome' = 'OVER_CAPACITY' AND d."reviewState" = 'CONFIRMED'),
                 'dismissed', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d.evidence ->> 'outcome' = 'OVER_CAPACITY' AND d."reviewState" = 'DISMISSED'),
                 'pending', count(*) FILTER (WHERE d.status = 'POTENTIAL_VIOLATION' AND d.evidence ->> 'outcome' = 'OVER_CAPACITY' AND d."reviewState" = 'PENDING')),
               'could_not_verify', count(*) FILTER (WHERE d.status = 'UNVERIFIABLE'))
        FROM public.safety_decisions d
       WHERE d."orgId" = p_org AND d."observedAt" >= p_since AND d."observedAt" < p_until AND d."ruleType" = 'OCCUPANCY'));

  -- ── corrective actions ────────────────────────────────────────────────────
  v := v || jsonb_build_object('actions', (
    SELECT jsonb_build_object(
             'opened', count(*) FILTER (WHERE a."createdAt" >= p_since AND a."createdAt" < p_until),
             'closed', count(*) FILTER (WHERE a.status = 'CLOSED' AND a."closedAt" >= p_since AND a."closedAt" < p_until),
             'open_now', count(*) FILTER (WHERE a.status IN ('OPEN', 'IN_PROGRESS')),
             'overdue_now', count(*) FILTER (WHERE a.status IN ('OPEN', 'IN_PROGRESS') AND a."dueDate" IS NOT NULL AND a."dueDate" < v_today),
             'average_days_to_close',
               round((avg(extract(epoch FROM (a."closedAt" - a."createdAt")) / 86400.0)
                      FILTER (WHERE a.status = 'CLOSED' AND a."closedAt" >= p_since AND a."closedAt" < p_until))::numeric, 1))
      FROM public.corrective_actions a
     WHERE a."orgId" = p_org));

  RETURN v;
END;
$$;
REVOKE ALL ON FUNCTION public.safety_report(UUID, TIMESTAMPTZ, TIMESTAMPTZ, TEXT) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.safety_report(UUID, TIMESTAMPTZ, TIMESTAMPTZ, TEXT) TO authenticated;

NOTIFY pgrst, 'reload schema';
