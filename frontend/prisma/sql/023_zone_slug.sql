-- VisionWorks — separate a zone's stable key from its readable name
--
-- THE BUG THIS FIXES
--
-- `_mirror_zone_to_postgres` wrote the pipeline's generated slug into
-- `zones.name`:
--
--     "name": zone_id        -- "zone_mtfirbfy"
--
-- despite receiving the name the user actually typed. So the employee desk
-- dropdown — which reads `zones.name` — listed rows like "zone_mtfgbh2z"
-- instead of "Prajwal's desk", and there was no way to tell which polygon was
-- which when assigning somebody a seat. Measured on the live database: five
-- zones, three of them named after their own slug.
--
-- Fixing the mirror to write the human name is most of the answer, but it
-- creates a second problem: the mirror matched existing rows BY that name, so
-- renaming a zone in the editor would stop matching and insert a duplicate.
-- The two jobs need two columns.
--
--   slug  — the pipeline's identifier. Stable, never shown, used to match.
--   name  — what a person reads. Free to change.
--
-- WHY NOT JUST MATCH ON THE POLYGON
--
-- Because a zone can be reshaped, and reshaping is exactly when the mirror
-- most needs to find the existing row rather than add another one.
--
-- Idempotent: safe to re-run.

BEGIN;

ALTER TABLE public.zones
  ADD COLUMN IF NOT EXISTS slug VARCHAR(64);

COMMENT ON COLUMN public.zones.slug IS
  'The CV pipeline''s stable zone identifier (local zones.zone_id). Never '
  'displayed; used to match a Postgres row to the pipeline row so a rename '
  'updates rather than duplicates. `name` is the human-readable label.';

-- Backfill. Rows mirrored before this column existed carry the slug in
-- `name`, which is precisely the bug — recognisable because the pipeline
-- generates `zone_<random>` and nobody types that.
UPDATE public.zones
   SET slug = name
 WHERE slug IS NULL
   AND name ~ '^zone_[a-z0-9]+$';

-- Everything else keeps its name and gets a slug derived from it, so the
-- matching key exists for every row.
UPDATE public.zones
   SET slug = name
 WHERE slug IS NULL;

-- One slug per camera. Two zones on one camera sharing an identifier would
-- make the mirror update whichever it happened to find first.
CREATE UNIQUE INDEX IF NOT EXISTS ux_zones_camera_slug
  ON public.zones ("cameraId", slug)
  WHERE slug IS NOT NULL;

-- The mirror looks rows up by (cameraId, slug) on every zone write.
CREATE INDEX IF NOT EXISTS ix_zones_slug
  ON public.zones (slug);

-- `zones` was created by Prisma, so `id` has no database default — the trap
-- migration 008 documented and 022 repaired for the identity tables. The
-- zone mirror inserts through PostgREST, which does not supply one.
ALTER TABLE public.zones ALTER COLUMN id SET DEFAULT gen_random_uuid();

COMMIT;
