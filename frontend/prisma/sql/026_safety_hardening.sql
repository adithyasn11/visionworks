-- VisionWorks — safety hardening, from the Step 1-10 test pass.
--
-- Applied after 025_safety.sql. Idempotent. Each item below is a defect an independent tester
-- reproduced against the live project:
--
--   1. A corrective action could be set back to OPEN on a DISMISSED decision (the CONFIRMED to
--      DISMISSED guard only looked at the actions at the moment of dismissal).
--   2. Any member, even a VIEWER, could write audit rows for safety actions ("decision reviewed",
--      "evidence viewed"), which weakens the claim that those actions are audited.
--   3. A manager could set attribution and timestamps the database should own when INSERTING
--      (closedById, createdAt, deletedAt on a new rule: a rule born soft-deleted skips the audit
--      row of soft_delete_safety_rule) and choose closedAt freely.
--   4. A soft-deleted organisation kept full access to its safety data.
--   5. Rule names, action titles and rule parameters had no sanity limits at the database, and a
--      review reason code was any text up to 24 characters.

-- ═══ 1. Reopening an action re-checks its decision ═════════════════════════
-- (the same check as on insert, run when an action leaves CLOSED / CANCELLED)
DROP TRIGGER IF EXISTS corrective_actions_check_reopen ON public.corrective_actions;
CREATE TRIGGER corrective_actions_check_reopen
  BEFORE UPDATE OF status ON public.corrective_actions
  FOR EACH ROW
  WHEN (OLD.status IN ('CLOSED','CANCELLED') AND NEW.status IN ('OPEN','IN_PROGRESS'))
  EXECUTE FUNCTION public.corrective_action_check_decision();

-- ═══ 3. The database owns attribution and timestamps ═══════════════════════
-- Applies to browser callers only (auth.uid() is NULL for the service role, whose values are kept).
CREATE OR REPLACE FUNCTION public.safety_stamp_actor()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = '' AS $$
DECLARE v_closed BOOLEAN;
BEGIN
  IF auth.uid() IS NULL THEN
    RETURN NEW;                                   -- the pipeline / service role: values are kept as given
  END IF;
  IF TG_TABLE_NAME = 'safety_rules' THEN
    IF TG_OP = 'INSERT' THEN
      NEW."createdById" := auth.uid();
      NEW."createdAt" := NOW();
      NEW."updatedAt" := NOW();
      NEW."deletedAt" := NULL;                    -- a rule is born alive; deleting it goes through soft_delete_safety_rule()
    END IF;
  ELSIF TG_TABLE_NAME = 'corrective_actions' THEN
    v_closed := NEW.status IN ('CLOSED','CANCELLED');
    IF TG_OP = 'INSERT' THEN
      NEW."createdById" := auth.uid();
      NEW."createdAt" := NOW();
      NEW."updatedAt" := NOW();
      NEW."closedById" := CASE WHEN v_closed THEN auth.uid() END;
      NEW."closedAt" := CASE WHEN v_closed THEN NOW() END;
    ELSE
      IF v_closed AND OLD.status NOT IN ('CLOSED','CANCELLED') THEN
        NEW."closedById" := auth.uid();           -- the transition is what is recorded, by whoever made it
        NEW."closedAt" := NOW();
      ELSIF v_closed THEN
        NEW."closedById" := OLD."closedById";     -- already closed: who and when do not change
        NEW."closedAt" := OLD."closedAt";
      ELSE
        NEW."closedById" := NULL;                 -- open again: the old closure is not carried over
        NEW."closedAt" := NULL;
        NEW."closingNote" := CASE WHEN OLD.status IN ('CLOSED','CANCELLED') THEN NULL ELSE NEW."closingNote" END;
      END IF;
    END IF;
    IF NEW."assignedToId" IS NOT NULL AND (TG_OP = 'INSERT' OR NEW."assignedToId" IS DISTINCT FROM OLD."assignedToId")
       AND NOT EXISTS (SELECT 1 FROM public.memberships m WHERE m."orgId" = NEW."orgId"
                        AND m."profileId" = NEW."assignedToId" AND m.status = 'ACTIVE') THEN
      RAISE EXCEPTION 'an action can only be assigned to an active member of the organisation';
    END IF;
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS corrective_actions_stamp_actor ON public.corrective_actions;
CREATE TRIGGER corrective_actions_stamp_actor BEFORE INSERT OR UPDATE ON public.corrective_actions
  FOR EACH ROW EXECUTE FUNCTION public.safety_stamp_actor();
-- (safety_rules_stamp_actor from 025 is BEFORE INSERT and now also resets createdAt/updatedAt/deletedAt)

-- ═══ 2. Safety audit rows are written by the database functions, not by members ═══
DROP POLICY IF EXISTS audit_insert ON public.audit_logs;
CREATE POLICY audit_insert ON public.audit_logs
  FOR INSERT TO authenticated
  WITH CHECK (
    "orgId" IN (SELECT public.user_org_ids())
    AND ("actorId" IS NULL OR "actorId" = (SELECT auth.uid()))
    -- review, evidence views and rule deletion are audited by SECURITY DEFINER functions and the
    -- service role; a member must not be able to write (or forge) those entries
    AND action NOT LIKE 'safety.%'
  );
-- TRUNCATE would empty an organisation's audit trail; no policy covers it, so remove the grant.
REVOKE TRUNCATE, REFERENCES, TRIGGER ON public.audit_logs FROM authenticated, anon;

-- ═══ 4. A soft-deleted organisation loses access to its safety data ══════════
-- The ids of the caller's organisations that have been soft-deleted. SECURITY DEFINER so the
-- answer does not depend on what the caller can read of `organisations` after deletion.
CREATE OR REPLACE FUNCTION public.deleted_org_ids()
RETURNS SETOF UUID LANGUAGE sql STABLE SECURITY DEFINER SET search_path = '' AS $$
  SELECT o.id FROM public.organisations o
   WHERE o."deletedAt" IS NOT NULL AND o.id IN (SELECT public.user_org_ids());
$$;
REVOKE ALL ON FUNCTION public.deleted_org_ids() FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.deleted_org_ids() TO authenticated;

DROP POLICY IF EXISTS safety_rule_select ON public.safety_rules;
CREATE POLICY safety_rule_select ON public.safety_rules FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()) AND "orgId" NOT IN (SELECT public.deleted_org_ids())
         AND "deletedAt" IS NULL);
DROP POLICY IF EXISTS safety_rule_insert ON public.safety_rules;
CREATE POLICY safety_rule_insert ON public.safety_rules FOR INSERT TO authenticated
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()) AND "orgId" NOT IN (SELECT public.deleted_org_ids()));
DROP POLICY IF EXISTS safety_rule_update ON public.safety_rules;
CREATE POLICY safety_rule_update ON public.safety_rules FOR UPDATE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()) AND "orgId" NOT IN (SELECT public.deleted_org_ids()))
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()) AND "orgId" NOT IN (SELECT public.deleted_org_ids()));

DROP POLICY IF EXISTS safety_event_select ON public.safety_events;
CREATE POLICY safety_event_select ON public.safety_events FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()) AND "orgId" NOT IN (SELECT public.deleted_org_ids()));
DROP POLICY IF EXISTS safety_decision_select ON public.safety_decisions;
CREATE POLICY safety_decision_select ON public.safety_decisions FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()) AND "orgId" NOT IN (SELECT public.deleted_org_ids()));

DROP POLICY IF EXISTS corrective_action_select ON public.corrective_actions;
CREATE POLICY corrective_action_select ON public.corrective_actions FOR SELECT TO authenticated
  USING ("orgId" IN (SELECT public.user_org_ids()) AND "orgId" NOT IN (SELECT public.deleted_org_ids()));
DROP POLICY IF EXISTS corrective_action_insert ON public.corrective_actions;
CREATE POLICY corrective_action_insert ON public.corrective_actions FOR INSERT TO authenticated
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()) AND "orgId" NOT IN (SELECT public.deleted_org_ids()));
DROP POLICY IF EXISTS corrective_action_update ON public.corrective_actions;
CREATE POLICY corrective_action_update ON public.corrective_actions FOR UPDATE TO authenticated
  USING ("orgId" IN (SELECT public.manage_org_ids()) AND "orgId" NOT IN (SELECT public.deleted_org_ids()))
  WITH CHECK ("orgId" IN (SELECT public.manage_org_ids()) AND "orgId" NOT IN (SELECT public.deleted_org_ids()));

-- the two functions that bypass RLS check it themselves
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
  -- the reasons a reviewer can give (the dashboard offers exactly these)
  IF p_reason IS NOT NULL AND p_reason NOT IN
     ('CORRECT','PPE_PRESENT','WRONG_PERSON','NOT_IN_ZONE','BAD_PICTURE','OTHER') THEN
    RAISE EXCEPTION 'unknown review reason';
  END IF;
  -- FOR UPDATE: serialises with a concurrent corrective-action insert (which takes FOR SHARE).
  SELECT * INTO v_row FROM public.safety_decisions WHERE id = p_decision_id FOR UPDATE;
  -- Not found, not yours, and an organisation that has been deleted all look the same.
  IF NOT FOUND OR v_row."orgId" NOT IN (SELECT public.manage_org_ids())
     OR v_row."orgId" IN (SELECT public.deleted_org_ids()) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF v_row.status = 'COMPLIANT' THEN
    RAISE EXCEPTION 'compliant decisions are not reviewed';
  END IF;
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

CREATE OR REPLACE FUNCTION public.soft_delete_safety_rule(p_rule_id UUID)
RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER SET search_path = ''
AS $$
DECLARE v_org UUID;
BEGIN
  SELECT r."orgId" INTO v_org FROM public.safety_rules r
   WHERE r.id = p_rule_id AND r."deletedAt" IS NULL;
  IF v_org IS NULL THEN RAISE EXCEPTION 'rule not found' USING ERRCODE = 'no_data_found'; END IF;
  IF v_org NOT IN (SELECT public.manage_org_ids()) OR v_org IN (SELECT public.deleted_org_ids()) THEN
    RAISE EXCEPTION 'not permitted' USING ERRCODE = 'insufficient_privilege';
  END IF;
  UPDATE public.safety_rules SET "deletedAt" = NOW(), "isEnabled" = FALSE WHERE id = p_rule_id;
  INSERT INTO public.audit_logs ("orgId", "actorId", action, "targetType", "targetId", metadata)
  VALUES (v_org, auth.uid(), 'safety.rule_deleted', 'safety_rule', p_rule_id::text, '{}'::jsonb);
END $$;
REVOKE ALL ON FUNCTION public.soft_delete_safety_rule(UUID) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.soft_delete_safety_rule(UUID) TO authenticated;

-- ═══ 5. Sanity limits ══════════════════════════════════════════════════════
-- NOT VALID: new and changed rows are checked; rows that already exist are not re-scanned.
ALTER TABLE public.safety_rules DROP CONSTRAINT IF EXISTS safety_rules_name_present;
ALTER TABLE public.safety_rules ADD CONSTRAINT safety_rules_name_present CHECK (length(btrim(name)) > 0) NOT VALID;
ALTER TABLE public.safety_rules DROP CONSTRAINT IF EXISTS safety_rules_params_small;
ALTER TABLE public.safety_rules ADD CONSTRAINT safety_rules_params_small CHECK (octet_length(params::text) <= 8000) NOT VALID;
ALTER TABLE public.corrective_actions DROP CONSTRAINT IF EXISTS corrective_actions_title_present;
ALTER TABLE public.corrective_actions ADD CONSTRAINT corrective_actions_title_present CHECK (length(btrim(title)) > 0) NOT VALID;

NOTIFY pgrst, 'reload schema';
