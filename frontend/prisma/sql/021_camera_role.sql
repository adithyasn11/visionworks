-- VisionWorks — camera role and inference width
--
-- Step 10 of IDENTITY_TRACKING_PLAN.md. Two columns on `cameras`, and nothing
-- else touched.
--
-- ═══════════════════════════════════════════════════════════════════════════
--  WHY A CAMERA NEEDS A ROLE
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Face recognition needs roughly 80 px between the eyes and degrades sharply
-- below 40. The plan measures what this pipeline actually produces:
--
--     distance   person height (640px frame)   eye-to-eye   viable?
--     2 m        300 px                        18 px        no
--     3 m        200 px                        12 px        no
--     5 m        120 px                         7 px        no
--
-- So face matching is not something to enable everywhere and hope. It runs at
-- ONE camera, positioned 1-2 m from a doorway where somebody walks straight at
-- it, and every other camera matches appearance instead — which OSNet does
-- happily at 128x64.
--
-- `role` is what makes that structural rather than a convention. A camera is
-- AREA unless somebody deliberately marks it DOOR, so face matching is off by
-- default in every deployment and turning it on is a visible act.
--
-- ═══════════════════════════════════════════════════════════════════════════
--  WHY THE WIDTH IS PER-CAMERA
-- ═══════════════════════════════════════════════════════════════════════════
--
-- `video_upload.py` resizes every frame to 640 px wide for throughput. On a
-- door camera that is self-defeating: it throws away the resolution the face
-- signal lives in, so the one camera that could recognise somebody is the one
-- least able to. A door camera keeps 1280.
--
-- NULL means "use the default for this role" — 640 for AREA, 1280 for DOOR —
-- rather than pinning a number that would then have to be migrated if the
-- default changed.
--
-- Idempotent. Apply AFTER 020_identity.sql.

-- ═══════════════════════════════════════════════════════════════════════════
--  ENUM
-- ═══════════════════════════════════════════════════════════════════════════

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'CameraRole') THEN
    CREATE TYPE "CameraRole" AS ENUM ('AREA', 'DOOR');
  END IF;
END $$;

-- ═══════════════════════════════════════════════════════════════════════════
--  COLUMNS
-- ═══════════════════════════════════════════════════════════════════════════

ALTER TABLE public.cameras
  ADD COLUMN IF NOT EXISTS role "CameraRole" NOT NULL DEFAULT 'AREA';

ALTER TABLE public.cameras
  ADD COLUMN IF NOT EXISTS "inferenceWidth" INTEGER;

DO $$
BEGIN
  -- A sane range. Below 320 nothing is detectable at all; above 3840 the
  -- pipeline would spend its whole budget on one camera. Both ends are bugs
  -- rather than choices, so the database rejects them.
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'cameras_inference_width_range') THEN
    ALTER TABLE public.cameras ADD CONSTRAINT cameras_inference_width_range
      CHECK ("inferenceWidth" IS NULL OR ("inferenceWidth" BETWEEN 320 AND 3840));
  END IF;
END $$;

-- Finding the door camera is a per-frame question on a live pipeline, so it
-- gets an index rather than a scan. Partial: DOOR cameras are a handful out of
-- however many an estate has, and the AREA rows would only bloat it.
CREATE INDEX IF NOT EXISTS cameras_org_role_idx
  ON public.cameras ("orgId", role)
  WHERE role = 'DOOR';

-- ═══════════════════════════════════════════════════════════════════════════
--  GRANTS
-- ═══════════════════════════════════════════════════════════════════════════
--
-- 004 revoked table-level SELECT on `cameras` and granted each readable column
-- explicitly, so that `rtspUrl` stays unreadable. A column added afterwards is
-- therefore NOT readable by default — it has to be named here, or the
-- dashboard silently cannot see a camera's role.
--
-- This is the same trap 004 documents and 020 hit again with
-- `face_templates.embedding`: a column-level grant list is a whitelist, and a
-- new column is outside it until somebody says otherwise.

GRANT SELECT (role, "inferenceWidth") ON public.cameras TO authenticated;
