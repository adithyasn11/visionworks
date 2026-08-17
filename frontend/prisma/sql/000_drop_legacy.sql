-- VisionWorks — retire the legacy schema
--
-- The original backend/app/db/supabase_schema.sql created three tables:
--
--     cameras, zones, activity_logs
--
-- They are superseded by the Prisma schema, which models the same domain
-- properly (org-scoped, RLS-enforced, with minute-bucket aggregation instead
-- of per-detection rows). Leaving them in place causes `prisma migrate` to
-- report schema drift and demand a full reset.
--
-- WHY NOT `prisma migrate reset`
--
-- Reset drops and recreates the whole database, including Supabase's
-- `auth.users` — every account you have signed up would be destroyed. Dropping
-- exactly the three legacy tables is the narrow, reversible action.
--
-- SAFETY: verified empty (0 rows in all three) before this was written. The
-- guard below re-checks at run time and aborts rather than deleting data, so
-- re-running this against a database where someone has since inserted rows
-- fails loudly instead of silently destroying them.
--
-- Note the old `activity_logs` design is also why the new schema exists: it
-- stored one row per person per frame, complete with `track_id` — the exact
-- per-person record the privacy model says must not be possible.

DO $$
DECLARE
  legacy   TEXT[] := ARRAY['activity_logs', 'zones', 'cameras'];
  t        TEXT;
  n        BIGINT;
  is_prisma BOOLEAN;
BEGIN
  FOREACH t IN ARRAY legacy LOOP
    -- Skip if the table does not exist (already dropped, or fresh database).
    IF NOT EXISTS (
      SELECT 1 FROM information_schema.tables
      WHERE table_schema = 'public' AND table_name = t
    ) THEN
      RAISE NOTICE 'skip %: not present', t;
      CONTINUE;
    END IF;

    -- The Prisma schema maps Camera -> "cameras" and Zone -> "zones", the same
    -- names. Telling them apart matters enormously: dropping the Prisma table
    -- would destroy real data. The legacy tables use snake_case columns
    -- (camera_id), the Prisma ones camelCase ("orgId"), so the presence of
    -- "orgId" is a reliable discriminator.
    SELECT EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'public' AND table_name = t AND column_name = 'orgId'
    ) INTO is_prisma;

    IF is_prisma THEN
      RAISE NOTICE 'skip %: this is the Prisma table (has "orgId"), not the legacy one', t;
      CONTINUE;
    END IF;

    EXECUTE format('SELECT count(*) FROM public.%I', t) INTO n;

    IF n > 0 THEN
      RAISE EXCEPTION
        'Refusing to drop public.% — it holds % row(s). Export them first, then re-run.', t, n
        USING HINT = 'This script only removes empty legacy tables.';
    END IF;

    EXECUTE format('DROP TABLE public.%I CASCADE', t);
    RAISE NOTICE 'dropped % (was empty)', t;
  END LOOP;
END $$;

-- The legacy schema also added activity_logs to the realtime publication.
-- Dropping the table removes it, but this is harmless and explicit.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime') THEN
    RAISE NOTICE 'supabase_realtime publication left intact';
  END IF;
END $$;
