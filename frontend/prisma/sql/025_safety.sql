-- VisionWorks — safety compliance: rules, measured events, decisions, corrective
-- actions, and the one human write (review).
--
-- Applied after 024_retire_productivity_tables.sql. Idempotent: CREATE ... IF NOT
-- EXISTS, DROP POLICY / TRIGGER IF EXISTS, CREATE OR REPLACE. Re-running is a no-op.
--
-- WHO MAY DO WHAT (measured by backend/tests/test_safety_rls.py, not assumed)
--
--   Actor            safety_rules                 safety_events   safety_decisions   review_safety_decision()   corrective_actions
--   VIEWER           select                       select          select             refused                    select
--   MANAGER / ADMIN  select, insert, update;      select          select             allowed (not on COMPLIANT) select, insert (CONFIRMED
--                    soft delete only through                                                                   decision only), update
--                    soft_delete_safety_rule()
--   Other org        nothing                      nothing         nothing            refused                    nothing
--   anon             no privileges                no privileges   no privileges      refused                    no privileges
--   service_role     all                          insert          insert             n/a                        all
--
-- There is deliberately no INSERT / UPDATE / DELETE policy on safety_events or
-- safety_decisions: the pipeline writes them with the service role, so a browser
-- cannot fabricate or alter a measurement. A policy cannot restrict WHICH columns
-- change, so review is a function that touches only the review columns and
-- always writes the audit row in the same transaction.

-- ═══ TABLES ═════════════════════════════════════════════════════════════════

-- Configuration: what to check. Writable by ADMIN and MANAGER.
CREATE TABLE IF NOT EXISTS public.safety_rules (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "orgId"      UUID NOT NULL REFERENCES public.organisations(id) ON DELETE CASCADE,
  "cameraId"   UUID REFERENCES public.cameras(id) ON DELETE CASCADE,
  "zoneId"     UUID REFERENCES public.zones(id)   ON DELETE CASCADE,
  name         VARCHAR(160) NOT NULL,
  type         VARCHAR(24)  NOT NULL,
  params       JSONB        NOT NULL DEFAULT '{}'::jsonb,
  severity     VARCHAR(10)  NOT NULL DEFAULT 'WARNING',
  "isEnabled"  BOOLEAN      NOT NULL DEFAULT TRUE,
  "minEvidenceConfidence" DOUBLE PRECISION NOT NULL DEFAULT 0.6,
  "createdById" UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
  "createdAt"  TIMESTAMPTZ(6) NOT NULL DEFAULT NOW(),
  "updatedAt"  TIMESTAMPTZ(6) NOT NULL DEFAULT NOW(),
  "deletedAt"  TIMESTAMPTZ(6),
  CONSTRAINT safety_rules_type_known CHECK (type IN
    ('PPE_ENTRY','PPE_CONTINUOUS','MAX_DWELL','REQUIRED_EXIT','OCCUPANCY','MAN_DOWN','WALK_ONLY')),
  CONSTRAINT safety_rules_severity_known CHECK (severity IN ('INFO','WARNING','CRITICAL')),
  CONSTRAINT safety_rules_confidence_range CHECK
    ("minEvidenceConfidence" >= 0 AND "minEvidenceConfidence" <= 1),
  CONSTRAINT safety_rules_params_object CHECK (jsonb_typeof(params) = 'object'),
  -- every rule except a camera-wide MAN_DOWN must be anchored to a zone
  CONSTRAINT safety_rules_zone_required CHECK (type = 'MAN_DOWN' OR "zoneId" IS NOT NULL)
);

-- Measured: what the pipeline saw. SELECT only for browsers.
CREATE TABLE IF NOT EXISTS public.safety_events (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "eventUid"   VARCHAR(36) NOT NULL,                      -- from SQLite; makes sync idempotent
  "orgId"      UUID NOT NULL REFERENCES public.organisations(id) ON DELETE CASCADE,
  "cameraId"   UUID NOT NULL REFERENCES public.cameras(id) ON DELETE CASCADE,
  "zoneId"     UUID REFERENCES public.zones(id) ON DELETE SET NULL,
  "workerKey"  VARCHAR(80) NOT NULL,
  "sessionKey" VARCHAR(40),                               -- pipeline session id (not an FK)
  "eventType"  VARCHAR(24) NOT NULL,
  "videoTimeS" DOUBLE PRECISION NOT NULL,
  "observedAt" TIMESTAMPTZ(6) NOT NULL,
  details      JSONB,
  CONSTRAINT safety_events_type_known CHECK ("eventType" IN
    ('ENTERED_ZONE','EXITED_ZONE','LOST_IN_ZONE','REAPPEARED',
     'DWELL_WARNING','DWELL_EXCEEDED','PPE_VERDICT_CHANGED')),
  CONSTRAINT safety_events_time_nonneg CHECK ("videoTimeS" >= 0),
  CONSTRAINT safety_events_uid_unique UNIQUE ("eventUid")
);

-- Measured + reviewed: the compliance decisions. SELECT only; reviewed via function.
CREATE TABLE IF NOT EXISTS public.safety_decisions (
  id           UUID PRIMARY KEY,                           -- supplied by the pipeline
  "orgId"      UUID NOT NULL REFERENCES public.organisations(id) ON DELETE CASCADE,
  "cameraId"   UUID NOT NULL REFERENCES public.cameras(id) ON DELETE CASCADE,
  "zoneId"     UUID REFERENCES public.zones(id) ON DELETE SET NULL,
  "ruleId"     UUID REFERENCES public.safety_rules(id) ON DELETE SET NULL,
  "ruleType"   VARCHAR(24) NOT NULL,
  severity     VARCHAR(10) NOT NULL,
  "workerKey"  VARCHAR(80) NOT NULL,
  "sessionKey" VARCHAR(40),
  status       VARCHAR(20) NOT NULL,
  reason       VARCHAR(500) NOT NULL,
  confidence   DOUBLE PRECISION NOT NULL,
  evidence     JSONB NOT NULL,
  thresholds   JSONB,
  "snapshotRef" VARCHAR(300),
  "videoTimeS" DOUBLE PRECISION NOT NULL,
  "observedAt" TIMESTAMPTZ(6) NOT NULL,
  -- review
  "reviewState"      VARCHAR(14) NOT NULL DEFAULT 'NOT_REQUIRED',
  "reviewedById"     UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
  "reviewedAt"       TIMESTAMPTZ(6),
  "reviewReasonCode" VARCHAR(24),
  "reviewNote"       VARCHAR(500),
  "createdAt"  TIMESTAMPTZ(6) NOT NULL DEFAULT NOW(),
  "updatedAt"  TIMESTAMPTZ(6) NOT NULL DEFAULT NOW(),
  CONSTRAINT safety_decisions_status_known CHECK
    (status IN ('COMPLIANT','POTENTIAL_VIOLATION','UNVERIFIABLE')),
  CONSTRAINT safety_decisions_type_known CHECK ("ruleType" IN
    ('PPE_ENTRY','PPE_CONTINUOUS','MAX_DWELL','REQUIRED_EXIT','OCCUPANCY','MAN_DOWN','WALK_ONLY')),
  CONSTRAINT safety_decisions_severity_known CHECK (severity IN ('INFO','WARNING','CRITICAL')),
  CONSTRAINT safety_decisions_confidence_range CHECK (confidence >= 0 AND confidence <= 1),
  CONSTRAINT safety_decisions_unverifiable_no_claim CHECK (status <> 'UNVERIFIABLE' OR confidence = 0),
  CONSTRAINT safety_decisions_time_nonneg CHECK ("videoTimeS" >= 0),
  CONSTRAINT safety_decisions_review_known CHECK
    ("reviewState" IN ('NOT_REQUIRED','PENDING','CONFIRMED','DISMISSED')),
  -- a compliant decision is never put in a review queue
  CONSTRAINT safety_decisions_compliant_no_review CHECK
    (status <> 'COMPLIANT' OR "reviewState" = 'NOT_REQUIRED'),
  -- a decision is reviewed exactly when it carries a review time. ("reviewedById"
  -- is deliberately not part of this: it becomes NULL through ON DELETE SET NULL
  -- when a reviewer's profile is removed, and that must stay legal.)
  CONSTRAINT safety_decisions_review_complete CHECK
    (("reviewState" IN ('CONFIRMED','DISMISSED')) = ("reviewedAt" IS NOT NULL))
);

-- Management: what was done about a confirmed violation. Writable by ADMIN and MANAGER.
CREATE TABLE IF NOT EXISTS public.corrective_actions (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  "orgId"        UUID NOT NULL REFERENCES public.organisations(id) ON DELETE CASCADE,
  "decisionId"   UUID NOT NULL REFERENCES public.safety_decisions(id) ON DELETE CASCADE,
  title          VARCHAR(200) NOT NULL,
  description    VARCHAR(2000),
  priority       VARCHAR(10)  NOT NULL DEFAULT 'WARNING',
  status         VARCHAR(12)  NOT NULL DEFAULT 'OPEN',
  "assignedToId" UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
  "dueDate"      DATE,
  "createdById"  UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
  "closedById"   UUID REFERENCES public.profiles(id) ON DELETE SET NULL,
  "closedAt"     TIMESTAMPTZ(6),
  "closingNote"  VARCHAR(1000),
  "createdAt"    TIMESTAMPTZ(6) NOT NULL DEFAULT NOW(),
  "updatedAt"    TIMESTAMPTZ(6) NOT NULL DEFAULT NOW(),
  CONSTRAINT corrective_actions_status_known CHECK
    (status IN ('OPEN','IN_PROGRESS','CLOSED','CANCELLED')),
  CONSTRAINT corrective_actions_priority_known CHECK (priority IN ('INFO','WARNING','CRITICAL')),
  -- finished exactly when it carries a finish time
  CONSTRAINT corrective_actions_closed_complete CHECK
    ((status IN ('CLOSED','CANCELLED')) = ("closedAt" IS NOT NULL))
);

-- ═══ TENANCY TRIGGERS ═══════════════════════════════════════════════════════
-- RLS checks the row's own "orgId", but a foreign key does not know about orgs:
-- without these, a manager of org A could anchor a rule to org B's zone, or open
-- an action on org B's decision. Both guards run for every writer, the service
-- role included.

-- A rule's camera and zone must belong to the rule's organisation, and a zone must
-- sit on the camera the rule names.
CREATE OR REPLACE FUNCTION public.safety_rule_check_scope()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF NEW."cameraId" IS NOT NULL AND NOT EXISTS
     (SELECT 1 FROM public.cameras c WHERE c.id = NEW."cameraId" AND c."orgId" = NEW."orgId"
         AND c."deletedAt" IS NULL) THEN
    RAISE EXCEPTION 'the rule''s camera belongs to another organisation (or has been deleted)';
  END IF;
  IF NEW."zoneId" IS NOT NULL AND NOT EXISTS
     (SELECT 1 FROM public.zones z
       WHERE z.id = NEW."zoneId" AND z."orgId" = NEW."orgId" AND z."deletedAt" IS NULL
         AND (NEW."cameraId" IS NULL OR z."cameraId" = NEW."cameraId")) THEN
    RAISE EXCEPTION 'the rule''s zone is not in this organisation, not on the rule''s camera, or has been deleted';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS safety_rules_check_scope ON public.safety_rules;
CREATE TRIGGER safety_rules_check_scope
  BEFORE INSERT OR UPDATE OF "orgId", "cameraId", "zoneId" ON public.safety_rules
  FOR EACH ROW EXECUTE FUNCTION public.safety_rule_check_scope();

-- An action may only be opened for a violation a reviewer CONFIRMED, in the same org.
-- SECURITY DEFINER because FOR SHARE needs UPDATE rights on safety_decisions, which
-- browsers deliberately do not have; the check compares organisations explicitly.
CREATE OR REPLACE FUNCTION public.corrective_action_check_decision()
RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  -- FOR SHARE: a review that is flipping this decision to DISMISSED right now
  -- (it takes FOR UPDATE) must finish first, so an action can never be opened on a
  -- decision that is being dismissed.
  PERFORM 1 FROM public.safety_decisions d
   WHERE d.id = NEW."decisionId" AND d."orgId" = NEW."orgId" AND d."reviewState" = 'CONFIRMED'
     FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'corrective actions need a confirmed decision in the same organisation';
  END IF;
  RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.corrective_action_check_decision() FROM PUBLIC, anon, authenticated;
DROP TRIGGER IF EXISTS corrective_actions_check_decision ON public.corrective_actions;
CREATE TRIGGER corrective_actions_check_decision
  BEFORE INSERT OR UPDATE OF "decisionId", "orgId" ON public.corrective_actions
  FOR EACH ROW EXECUTE FUNCTION public.corrective_action_check_decision();

-- Who created / closed something is the signed-in user, never a value the browser
-- sends; an assignee must be an active member of the organisation. (The service
-- role has no auth.uid(), so values it supplies are kept.)
CREATE OR REPLACE FUNCTION public.safety_stamp_actor()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF TG_TABLE_NAME = 'safety_rules' THEN
    IF TG_OP = 'INSERT' AND auth.uid() IS NOT NULL THEN NEW."createdById" := auth.uid(); END IF;
  ELSIF TG_TABLE_NAME = 'corrective_actions' THEN
    IF TG_OP = 'INSERT' AND auth.uid() IS NOT NULL THEN NEW."createdById" := auth.uid(); END IF;
    IF NEW.status IN ('CLOSED','CANCELLED') AND (TG_OP = 'INSERT' OR OLD.status NOT IN ('CLOSED','CANCELLED'))
       AND auth.uid() IS NOT NULL THEN
      NEW."closedById" := auth.uid();
    END IF;
    IF NEW."assignedToId" IS NOT NULL AND (TG_OP = 'INSERT' OR NEW."assignedToId" IS DISTINCT FROM OLD."assignedToId")
       AND NOT EXISTS (SELECT 1 FROM public.memberships m WHERE m."orgId" = NEW."orgId"
                        AND m."profileId" = NEW."assignedToId" AND m.status = 'ACTIVE') THEN
      RAISE EXCEPTION 'an action can only be assigned to an active member of the organisation';
    END IF;
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS safety_rules_stamp_actor ON public.safety_rules;
CREATE TRIGGER safety_rules_stamp_actor BEFORE INSERT ON public.safety_rules
  FOR EACH ROW EXECUTE FUNCTION public.safety_stamp_actor();
DROP TRIGGER IF EXISTS corrective_actions_stamp_actor ON public.corrective_actions;
CREATE TRIGGER corrective_actions_stamp_actor BEFORE INSERT OR UPDATE ON public.corrective_actions
  FOR EACH ROW EXECUTE FUNCTION public.safety_stamp_actor();

-- ═══ EXISTING OBJECTS: two columns and one enum value ═══════════════════════

-- Zone safety type, separate from the existing ZoneType enum (left untouched).
ALTER TABLE public.zones ADD COLUMN IF NOT EXISTS "safetyType" VARCHAR(20);
ALTER TABLE public.zones DROP CONSTRAINT IF EXISTS zones_safety_type_known;
ALTER TABLE public.zones ADD CONSTRAINT zones_safety_type_known CHECK
  ("safetyType" IS NULL OR "safetyType" IN ('PPE_CHECKPOINT','APPROACH','RESTRICTED','EXIT','GENERAL'));

-- Evidence snapshots (Step 9) can be switched off per organisation.
ALTER TABLE public.organisations
  ADD COLUMN IF NOT EXISTS "safetyEvidenceEnabled" BOOLEAN NOT NULL DEFAULT TRUE;

-- Safety reports reuse the existing reports table. (On a Postgres older than 12 this
-- one line cannot run inside a transaction block: run it on its own.)
ALTER TYPE public."ReportKind" ADD VALUE IF NOT EXISTS 'SAFETY_SUMMARY';

-- ═══ INDEXES (the read paths, plus FK indexes so cascades do not scan) ══════
CREATE INDEX IF NOT EXISTS safety_rules_org_enabled_idx   ON public.safety_rules ("orgId", "isEnabled");
CREATE INDEX IF NOT EXISTS safety_rules_zone_idx          ON public.safety_rules ("zoneId");
CREATE INDEX IF NOT EXISTS safety_rules_camera_idx        ON public.safety_rules ("cameraId");
CREATE INDEX IF NOT EXISTS safety_rules_created_by_idx    ON public.safety_rules ("createdById");

CREATE INDEX IF NOT EXISTS safety_events_org_observed_idx   ON public.safety_events ("orgId", "observedAt" DESC);
CREATE INDEX IF NOT EXISTS safety_events_zone_observed_idx  ON public.safety_events ("zoneId", "observedAt");
CREATE INDEX IF NOT EXISTS safety_events_camera_idx         ON public.safety_events ("cameraId");
CREATE INDEX IF NOT EXISTS safety_events_worker_idx         ON public.safety_events ("workerKey", "observedAt");

CREATE INDEX IF NOT EXISTS safety_decisions_org_observed_idx  ON public.safety_decisions ("orgId", "observedAt" DESC);
CREATE INDEX IF NOT EXISTS safety_decisions_org_status_idx    ON public.safety_decisions ("orgId", status, "observedAt" DESC);
-- the review queue (Prisma cannot express a partial index, so this one lives only here)
CREATE INDEX IF NOT EXISTS safety_decisions_pending_idx       ON public.safety_decisions ("orgId", "observedAt")
  WHERE status = 'POTENTIAL_VIOLATION' AND "reviewState" = 'PENDING';
CREATE INDEX IF NOT EXISTS safety_decisions_zone_idx          ON public.safety_decisions ("zoneId", "observedAt");
CREATE INDEX IF NOT EXISTS safety_decisions_camera_idx        ON public.safety_decisions ("cameraId");
CREATE INDEX IF NOT EXISTS safety_decisions_rule_idx          ON public.safety_decisions ("ruleId");
CREATE INDEX IF NOT EXISTS safety_decisions_reviewer_idx      ON public.safety_decisions ("reviewedById");
CREATE INDEX IF NOT EXISTS safety_decisions_session_idx       ON public.safety_decisions ("orgId", "sessionKey");

CREATE INDEX IF NOT EXISTS corrective_actions_org_status_idx ON public.corrective_actions ("orgId", status, "dueDate");
CREATE INDEX IF NOT EXISTS corrective_actions_decision_idx   ON public.corrective_actions ("decisionId");
CREATE INDEX IF NOT EXISTS corrective_actions_assignee_idx   ON public.corrective_actions ("assignedToId");
CREATE INDEX IF NOT EXISTS corrective_actions_created_by_idx ON public.corrective_actions ("createdById");
CREATE INDEX IF NOT EXISTS corrective_actions_closed_by_idx  ON public.corrective_actions ("closedById");

-- ═══ updatedAt maintained in the database (Prisma's @updatedAt is client-side) ═
DROP TRIGGER IF EXISTS safety_rules_touch_updated_at ON public.safety_rules;
CREATE TRIGGER safety_rules_touch_updated_at BEFORE UPDATE ON public.safety_rules
  FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
DROP TRIGGER IF EXISTS safety_decisions_touch_updated_at ON public.safety_decisions;
CREATE TRIGGER safety_decisions_touch_updated_at BEFORE UPDATE ON public.safety_decisions
  FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();
DROP TRIGGER IF EXISTS corrective_actions_touch_updated_at ON public.corrective_actions;
CREATE TRIGGER corrective_actions_touch_updated_at BEFORE UPDATE ON public.corrective_actions
  FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

-- ═══ ROW LEVEL SECURITY ═════════════════════════════════════════════════════
-- Every policy is `"orgId" IN (SELECT helper())`, never a boolean helper call, so
-- the planner evaluates the helper once (see 002_auth_triggers.sql).
ALTER TABLE public.safety_rules       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.safety_events      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.safety_decisions   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.corrective_actions ENABLE ROW LEVEL SECURITY;

-- rules: everyone in the org reads; ADMIN + MANAGER write
DROP POLICY IF EXISTS safety_rule_select ON public.safety_rules;
CREATE POLICY safety_rule_select ON public.safety_rules FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()) AND "deletedAt" IS NULL);
DROP POLICY IF EXISTS safety_rule_insert ON public.safety_rules;
CREATE POLICY safety_rule_insert ON public.safety_rules FOR INSERT TO authenticated
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));
DROP POLICY IF EXISTS safety_rule_update ON public.safety_rules;
CREATE POLICY safety_rule_update ON public.safety_rules FOR UPDATE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()))
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));
-- No DELETE policy: rules are soft-deleted ("deletedAt") so old decisions keep their
-- reference. "deletedAt" is not an updatable column for browsers (see GRANTS), so soft
-- delete goes through soft_delete_safety_rule() below, which also writes the audit row.

-- events and decisions: measured data. SELECT only. Written by the service role.
DROP POLICY IF EXISTS safety_event_select ON public.safety_events;
CREATE POLICY safety_event_select ON public.safety_events FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()));
DROP POLICY IF EXISTS safety_decision_select ON public.safety_decisions;
CREATE POLICY safety_decision_select ON public.safety_decisions FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()));
-- deliberately NO insert / update / delete policy on events or decisions

-- corrective actions: everyone in the org reads; ADMIN + MANAGER create and update; no delete (cancel instead)
DROP POLICY IF EXISTS corrective_action_select ON public.corrective_actions;
CREATE POLICY corrective_action_select ON public.corrective_actions FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()));
DROP POLICY IF EXISTS corrective_action_insert ON public.corrective_actions;
CREATE POLICY corrective_action_insert ON public.corrective_actions FOR INSERT TO authenticated
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));
DROP POLICY IF EXISTS corrective_action_update ON public.corrective_actions;
CREATE POLICY corrective_action_update ON public.corrective_actions FOR UPDATE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()))
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()));

-- ═══ GRANTS ═════════════════════════════════════════════════════════════════
-- Supabase's default privileges hand every new public table ALL rights for anon and
-- authenticated (including DELETE and TRUNCATE). RLS would still refuse them, but
-- the grant is the first wall: take everything back, then give exactly what is meant.
REVOKE ALL ON public.safety_rules, public.safety_events, public.safety_decisions,
              public.corrective_actions FROM anon, authenticated;
GRANT SELECT, INSERT ON public.safety_rules, public.corrective_actions TO authenticated;
-- UPDATE is granted per column. "deletedAt" is left out on purpose: a policy cannot
-- stop a manager soft-deleting a rule directly (RLS checks the new row against the
-- select policy only when the statement has RETURNING, which many clients omit), and
-- that would skip the audit row. Ownership and attribution columns are immutable too.
GRANT UPDATE ("cameraId", "zoneId", name, type, params, severity, "isEnabled", "minEvidenceConfidence")
  ON public.safety_rules TO authenticated;
GRANT UPDATE (title, description, priority, status, "assignedToId", "dueDate", "closedAt", "closingNote")
  ON public.corrective_actions TO authenticated;
GRANT SELECT ON public.safety_events, public.safety_decisions TO authenticated;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE
      ON public.safety_rules, public.safety_events, public.safety_decisions,
         public.corrective_actions TO service_role;
  END IF;
END $$;

-- ═══ THE ONE HUMAN WRITE ON A DECISION: REVIEW ══════════════════════════════
CREATE OR REPLACE FUNCTION public.review_safety_decision(
  p_decision_id UUID, p_state TEXT, p_reason TEXT DEFAULT NULL, p_note TEXT DEFAULT NULL)
RETURNS public.safety_decisions
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ''
AS $$
DECLARE v_row public.safety_decisions; v_prev_state TEXT; v_prev_by UUID;
BEGIN
  IF p_state NOT IN ('CONFIRMED','DISMISSED') THEN
    RAISE EXCEPTION 'review state must be CONFIRMED or DISMISSED';
  END IF;
  -- FOR UPDATE: serialises with a concurrent corrective-action insert (which takes
  -- FOR SHARE on the decision), so a decision cannot be dismissed while an action
  -- is being opened on it.
  SELECT * INTO v_row FROM public.safety_decisions WHERE id = p_decision_id FOR UPDATE;
  -- Not found and not yours look the same on purpose: the answer must not tell a
  -- caller whether a decision id exists in somebody else's organisation.
  IF NOT FOUND OR v_row."orgId" NOT IN (SELECT public.manage_org_ids()) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF v_row.status = 'COMPLIANT' THEN
    RAISE EXCEPTION 'compliant decisions are not reviewed';
  END IF;
  -- a confirmed decision that already has corrective actions cannot be flipped to DISMISSED
  IF v_row."reviewState" = 'CONFIRMED' AND p_state = 'DISMISSED' AND EXISTS
     (SELECT 1 FROM public.corrective_actions a
       WHERE a."decisionId" = v_row.id AND a.status <> 'CANCELLED') THEN
    RAISE EXCEPTION 'cancel the corrective actions before dismissing this decision';
  END IF;
  v_prev_state := v_row."reviewState";
  v_prev_by := v_row."reviewedById";
  UPDATE public.safety_decisions
     SET "reviewState" = p_state, "reviewedById" = auth.uid(), "reviewedAt" = NOW(),
         "reviewReasonCode" = p_reason, "reviewNote" = LEFT(p_note, 500)
   WHERE id = p_decision_id
   RETURNING * INTO v_row;
  INSERT INTO public.audit_logs ("orgId", "actorId", action, "targetType", "targetId", metadata)
  VALUES (v_row."orgId", auth.uid(), 'safety.decision_reviewed', 'safety_decision',
          v_row.id::text, jsonb_build_object('state', p_state, 'reason', p_reason,
                                             'previousState', v_prev_state, 'previousReviewerId', v_prev_by));
  RETURN v_row;
END;
$$;
REVOKE ALL ON FUNCTION public.review_safety_decision(UUID, TEXT, TEXT, TEXT) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.review_safety_decision(UUID, TEXT, TEXT, TEXT) TO authenticated;

-- ═══ RULE SOFT DELETE ═══════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION public.soft_delete_safety_rule(p_rule_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = ''
AS $$
DECLARE v_org UUID;
BEGIN
  SELECT r."orgId" INTO v_org FROM public.safety_rules r
   WHERE r.id = p_rule_id AND r."deletedAt" IS NULL;
  IF v_org IS NULL THEN RAISE EXCEPTION 'rule not found' USING ERRCODE = 'no_data_found'; END IF;
  -- explicit authorisation: SECURITY DEFINER bypasses RLS
  IF v_org NOT IN (SELECT public.manage_org_ids()) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  UPDATE public.safety_rules SET "deletedAt" = NOW(), "isEnabled" = FALSE WHERE id = p_rule_id;
  INSERT INTO public.audit_logs ("orgId", "actorId", action, "targetType", "targetId", metadata)
  VALUES (v_org, auth.uid(), 'safety.rule_deleted', 'safety_rule', p_rule_id::text, '{}'::jsonb);
END $$;
REVOKE ALL ON FUNCTION public.soft_delete_safety_rule(UUID) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.soft_delete_safety_rule(UUID) TO authenticated;

-- ═══ RETENTION ══════════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION public.purge_safety_data()
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  -- raw events: past the organisation's retention window
  WITH gone AS (
    DELETE FROM public.safety_events e
     USING public.organisations o
     WHERE e."orgId" = o.id AND e."observedAt" < NOW() - make_interval(days => o."dataRetentionDays")
    RETURNING e."orgId")
  INSERT INTO public.audit_logs ("orgId", action, "targetType", metadata)
  SELECT "orgId", 'safety.events_purged', 'safety_event', jsonb_build_object('count', count(*))
    FROM gone GROUP BY "orgId";

  -- decisions are the compliance record: kept twice as long as raw events, and never
  -- while a violation still awaits review or a corrective action is still open
  -- (deleting the decision would cascade away the action)
  WITH gone AS (
    DELETE FROM public.safety_decisions d
     USING public.organisations o
     WHERE d."orgId" = o.id
       AND d."observedAt" < NOW() - make_interval(days => 2 * o."dataRetentionDays")
       AND NOT (d.status = 'POTENTIAL_VIOLATION' AND d."reviewState" = 'PENDING')
       AND NOT EXISTS (SELECT 1 FROM public.corrective_actions a
                        WHERE a."decisionId" = d.id AND a.status IN ('OPEN','IN_PROGRESS'))
    RETURNING d."orgId")
  INSERT INTO public.audit_logs ("orgId", action, "targetType", metadata)
  SELECT "orgId", 'safety.decisions_purged', 'safety_decision', jsonb_build_object('count', count(*))
    FROM gone GROUP BY "orgId";
END;
$$;
REVOKE ALL ON FUNCTION public.purge_safety_data() FROM PUBLIC, anon, authenticated;
-- the grant to service_role must be stated or the job fails with "permission denied"
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN
    GRANT EXECUTE ON FUNCTION public.purge_safety_data() TO service_role;
  END IF;
  -- daily schedule, guarded so the file still applies without pg_cron
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    PERFORM cron.unschedule('visionworks-purge-safety')
      WHERE EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'visionworks-purge-safety');
    PERFORM cron.schedule('visionworks-purge-safety', '45 3 * * *', 'SELECT public.purge_safety_data()');
  END IF;
END $$;

-- Let PostgREST see the new tables and functions without waiting for its own refresh.
NOTIFY pgrst, 'reload schema';
