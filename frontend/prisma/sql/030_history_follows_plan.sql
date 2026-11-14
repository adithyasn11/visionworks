-- 030_history_follows_plan.sql
--
-- Upgrading did not raise the history window. A Starter organisation keeps 7 days; after switching to Growth
-- (90 days) its `dataRetentionDays` stayed at 7, so the nightly purge kept deleting everything older than a
-- week although the plan paid for 90. The same happened when an expired plan was renewed.
--
-- Now, when the plan in force goes UP (an upgrade, or a renewal after expiry) and the organisation's history
-- was at the old plan's limit, it moves to the new plan's limit. A shorter value someone chose on purpose is
-- left alone, and going down never shortens it here (the purge already keeps only the plan's limit, 029).
--
-- Idempotent: safe to run again.

CREATE OR REPLACE FUNCTION public.history_follows_plan()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_old INT;
  v_new INT;
BEGIN
  SELECT max_retention_days INTO v_old FROM public.plan_limits
   WHERE plan = public.effective_plan(OLD.plan, OLD."planRenewsAt");
  SELECT max_retention_days INTO v_new FROM public.plan_limits
   WHERE plan = public.effective_plan(NEW.plan, NEW."planRenewsAt");
  IF v_old IS NOT NULL AND v_new IS NOT NULL AND v_new > v_old AND NEW."dataRetentionDays" >= v_old
     AND NEW."dataRetentionDays" < v_new THEN
    NEW."dataRetentionDays" := v_new;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS org_history_follows_plan ON public.organisations;
CREATE TRIGGER org_history_follows_plan
  BEFORE UPDATE OF plan, "planRenewsAt" ON public.organisations
  FOR EACH ROW EXECUTE FUNCTION public.history_follows_plan();
