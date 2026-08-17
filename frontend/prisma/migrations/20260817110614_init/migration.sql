-- CreateEnum
CREATE TYPE "Role" AS ENUM ('ADMIN', 'MANAGER', 'VIEWER');

-- CreateEnum
CREATE TYPE "MembershipStatus" AS ENUM ('INVITED', 'ACTIVE', 'SUSPENDED');

-- CreateEnum
CREATE TYPE "SourceType" AS ENUM ('UPLOAD', 'RTSP', 'WEBCAM');

-- CreateEnum
CREATE TYPE "CameraStatus" AS ENUM ('ACTIVE', 'INACTIVE', 'ERROR');

-- CreateEnum
CREATE TYPE "ZoneType" AS ENUM ('WORKSTATION', 'MEETING', 'BREAK', 'CORRIDOR', 'RECEPTION', 'OTHER');

-- CreateEnum
CREATE TYPE "Posture" AS ENUM ('SITTING', 'STANDING', 'WALKING', 'AWAY');

-- CreateEnum
CREATE TYPE "SessionStatus" AS ENUM ('QUEUED', 'PROCESSING', 'DONE', 'ERROR', 'CANCELLED');

-- CreateEnum
CREATE TYPE "SessionKind" AS ENUM ('VIDEO_UPLOAD', 'LIVE_WEBCAM', 'LIVE_RTSP');

-- CreateEnum
CREATE TYPE "AlertType" AS ENUM ('SEDENTARY', 'OVERCROWDING', 'UNDERUTILISATION', 'ZONE_EMPTY', 'CAMERA_OFFLINE');

-- CreateEnum
CREATE TYPE "AlertSeverity" AS ENUM ('INFO', 'WARNING', 'CRITICAL');

-- CreateEnum
CREATE TYPE "AlertState" AS ENUM ('OPEN', 'ACKNOWLEDGED', 'RESOLVED', 'MUTED');

-- CreateEnum
CREATE TYPE "ReportKind" AS ENUM ('UTILISATION_SUMMARY', 'ZONE_COMPARISON', 'POSTURE_BREAKDOWN', 'RAW_BUCKETS');

-- CreateEnum
CREATE TYPE "ReportFormat" AS ENUM ('CSV', 'PDF');

-- CreateEnum
CREATE TYPE "ReportStatus" AS ENUM ('QUEUED', 'GENERATING', 'READY', 'FAILED', 'EXPIRED');

-- CreateEnum
CREATE TYPE "ThemePreference" AS ENUM ('SYSTEM', 'LIGHT', 'DARK');

-- CreateTable
CREATE TABLE "organisations" (
    "id" UUID NOT NULL,
    "name" VARCHAR(160) NOT NULL,
    "slug" VARCHAR(80) NOT NULL,
    "timezone" VARCHAR(64) NOT NULL DEFAULT 'UTC',
    "dataRetentionDays" INTEGER NOT NULL DEFAULT 90,
    "purgeVideoAfterProcessing" BOOLEAN NOT NULL DEFAULT true,
    "defaultSedentaryThresholdMinutes" INTEGER NOT NULL DEFAULT 60,
    "defaultUtilisationFloorPct" DOUBLE PRECISION NOT NULL DEFAULT 30,
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL,
    "deletedAt" TIMESTAMPTZ(6),

    CONSTRAINT "organisations_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "profiles" (
    "id" UUID NOT NULL,
    "email" VARCHAR(320) NOT NULL,
    "fullName" VARCHAR(160),
    "avatarUrl" VARCHAR(1024),
    "jobTitle" VARCHAR(120),
    "themePreference" "ThemePreference" NOT NULL DEFAULT 'SYSTEM',
    "timezone" VARCHAR(64),
    "currentOrgId" UUID,
    "onboardedAt" TIMESTAMPTZ(6),
    "lastSeenAt" TIMESTAMPTZ(6),
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "profiles_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "memberships" (
    "id" UUID NOT NULL,
    "orgId" UUID NOT NULL,
    "profileId" UUID,
    "role" "Role" NOT NULL,
    "status" "MembershipStatus" NOT NULL DEFAULT 'INVITED',
    "invitedEmail" VARCHAR(320) NOT NULL,
    "inviteTokenHash" VARCHAR(64),
    "inviteExpiresAt" TIMESTAMPTZ(6),
    "invitedById" UUID,
    "acceptedAt" TIMESTAMPTZ(6),
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "memberships_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "sites" (
    "id" UUID NOT NULL,
    "orgId" UUID NOT NULL,
    "name" VARCHAR(160) NOT NULL,
    "location" VARCHAR(255),
    "timezone" VARCHAR(64),
    "totalCapacity" SMALLINT,
    "workdayStartMinute" INTEGER NOT NULL DEFAULT 540,
    "workdayEndMinute" INTEGER NOT NULL DEFAULT 1080,
    "workdays" INTEGER[] DEFAULT ARRAY[1, 2, 3, 4, 5]::INTEGER[],
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL,
    "deletedAt" TIMESTAMPTZ(6),

    CONSTRAINT "sites_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "cameras" (
    "id" UUID NOT NULL,
    "orgId" UUID NOT NULL,
    "siteId" UUID,
    "name" VARCHAR(160) NOT NULL,
    "description" VARCHAR(500),
    "sourceType" "SourceType" NOT NULL,
    "rtspUrl" VARCHAR(1024),
    "deviceIndex" SMALLINT,
    "fpsTarget" INTEGER NOT NULL DEFAULT 8,
    "frameWidth" SMALLINT,
    "frameHeight" SMALLINT,
    "homographyMatrix" JSONB,
    "homographyPoints" JSONB,
    "status" "CameraStatus" NOT NULL DEFAULT 'INACTIVE',
    "lastSeenAt" TIMESTAMPTZ(6),
    "lastErrorMessage" VARCHAR(500),
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL,
    "deletedAt" TIMESTAMPTZ(6),

    CONSTRAINT "cameras_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "zones" (
    "id" UUID NOT NULL,
    "orgId" UUID NOT NULL,
    "cameraId" UUID NOT NULL,
    "siteId" UUID,
    "name" VARCHAR(160) NOT NULL,
    "zoneType" "ZoneType" NOT NULL DEFAULT 'WORKSTATION',
    "polygon" JSONB NOT NULL,
    "capacity" SMALLINT,
    "colour" VARCHAR(9),
    "sedentaryThresholdMinutes" SMALLINT,
    "utilisationFloorPct" DOUBLE PRECISION,
    "excludeFromUtilisation" BOOLEAN NOT NULL DEFAULT false,
    "createdById" UUID,
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL,
    "deletedAt" TIMESTAMPTZ(6),

    CONSTRAINT "zones_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "zone_minute_stats" (
    "id" UUID NOT NULL,
    "orgId" UUID NOT NULL,
    "siteId" UUID,
    "cameraId" UUID NOT NULL,
    "zoneId" UUID NOT NULL,
    "bucketStart" TIMESTAMPTZ(6) NOT NULL,
    "occupancyMax" SMALLINT NOT NULL,
    "occupancyAvg" DOUBLE PRECISION NOT NULL,
    "occupancyMin" SMALLINT NOT NULL,
    "sittingFrames" INTEGER NOT NULL DEFAULT 0,
    "standingFrames" INTEGER NOT NULL DEFAULT 0,
    "walkingFrames" INTEGER NOT NULL DEFAULT 0,
    "sampleFrames" INTEGER NOT NULL DEFAULT 0,
    "avgActivityScore" DOUBLE PRECISION NOT NULL DEFAULT 0,
    "totalDwellSeconds" INTEGER NOT NULL DEFAULT 0,
    "uniqueTrackCount" SMALLINT NOT NULL DEFAULT 0,
    "sessionId" UUID,
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "zone_minute_stats_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "zone_day_stats" (
    "id" UUID NOT NULL,
    "orgId" UUID NOT NULL,
    "siteId" UUID,
    "cameraId" UUID NOT NULL,
    "zoneId" UUID NOT NULL,
    "statDate" DATE NOT NULL,
    "peakOccupancy" SMALLINT NOT NULL,
    "avgOccupancy" DOUBLE PRECISION NOT NULL,
    "peakHour" SMALLINT,
    "occupiedMinutes" INTEGER NOT NULL,
    "utilisationPct" DOUBLE PRECISION NOT NULL,
    "sittingRatio" DOUBLE PRECISION NOT NULL DEFAULT 0,
    "standingRatio" DOUBLE PRECISION NOT NULL DEFAULT 0,
    "walkingRatio" DOUBLE PRECISION NOT NULL DEFAULT 0,
    "totalDwellSeconds" INTEGER NOT NULL DEFAULT 0,
    "avgActivityScore" DOUBLE PRECISION NOT NULL DEFAULT 0,
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL,

    CONSTRAINT "zone_day_stats_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "analysis_sessions" (
    "id" UUID NOT NULL,
    "orgId" UUID NOT NULL,
    "cameraId" UUID,
    "kind" "SessionKind" NOT NULL,
    "status" "SessionStatus" NOT NULL DEFAULT 'QUEUED',
    "sourceFilename" VARCHAR(255),
    "sourceSizeBytes" BIGINT,
    "storagePath" VARCHAR(1024),
    "totalFrames" INTEGER,
    "processedFrames" INTEGER NOT NULL DEFAULT 0,
    "fpsAchieved" DOUBLE PRECISION,
    "durationSeconds" INTEGER,
    "coverageStart" TIMESTAMPTZ(6),
    "coverageEnd" TIMESTAMPTZ(6),
    "errorMessage" VARCHAR(1000),
    "startedById" UUID,
    "queuedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "startedAt" TIMESTAMPTZ(6),
    "finishedAt" TIMESTAMPTZ(6),

    CONSTRAINT "analysis_sessions_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "alert_rules" (
    "id" UUID NOT NULL,
    "orgId" UUID NOT NULL,
    "name" VARCHAR(160) NOT NULL,
    "type" "AlertType" NOT NULL,
    "severity" "AlertSeverity" NOT NULL DEFAULT 'WARNING',
    "isEnabled" BOOLEAN NOT NULL DEFAULT true,
    "zoneId" UUID,
    "cameraId" UUID,
    "thresholdValue" DOUBLE PRECISION NOT NULL,
    "sustainedMinutes" INTEGER NOT NULL DEFAULT 5,
    "cooldownMinutes" INTEGER NOT NULL DEFAULT 30,
    "onlyDuringWorkHours" BOOLEAN NOT NULL DEFAULT true,
    "notifyEmail" BOOLEAN NOT NULL DEFAULT false,
    "createdById" UUID,
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL,
    "deletedAt" TIMESTAMPTZ(6),

    CONSTRAINT "alert_rules_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "alerts" (
    "id" UUID NOT NULL,
    "orgId" UUID NOT NULL,
    "ruleId" UUID NOT NULL,
    "zoneId" UUID,
    "cameraId" UUID,
    "state" "AlertState" NOT NULL DEFAULT 'OPEN',
    "severity" "AlertSeverity" NOT NULL,
    "triggeredValue" DOUBLE PRECISION NOT NULL,
    "thresholdValue" DOUBLE PRECISION NOT NULL,
    "message" VARCHAR(500) NOT NULL,
    "triggeredAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "clearedAt" TIMESTAMPTZ(6),
    "acknowledgedAt" TIMESTAMPTZ(6),
    "acknowledgedById" UUID,
    "resolvedAt" TIMESTAMPTZ(6),
    "resolvedById" UUID,
    "resolutionNote" VARCHAR(1000),

    CONSTRAINT "alerts_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "reports" (
    "id" UUID NOT NULL,
    "orgId" UUID NOT NULL,
    "kind" "ReportKind" NOT NULL,
    "format" "ReportFormat" NOT NULL,
    "status" "ReportStatus" NOT NULL DEFAULT 'QUEUED',
    "title" VARCHAR(200) NOT NULL,
    "rangeStart" TIMESTAMPTZ(6) NOT NULL,
    "rangeEnd" TIMESTAMPTZ(6) NOT NULL,
    "zoneIds" UUID[],
    "siteIds" UUID[],
    "filePath" VARCHAR(1024),
    "fileSizeBytes" BIGINT,
    "downloadTokenHash" VARCHAR(64),
    "expiresAt" TIMESTAMPTZ(6),
    "errorMessage" VARCHAR(1000),
    "requestedById" UUID,
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "completedAt" TIMESTAMPTZ(6),

    CONSTRAINT "reports_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "audit_logs" (
    "id" UUID NOT NULL,
    "orgId" UUID NOT NULL,
    "actorId" UUID,
    "actorEmail" VARCHAR(320),
    "action" VARCHAR(80) NOT NULL,
    "targetType" VARCHAR(60),
    "targetId" VARCHAR(64),
    "metadata" JSONB,
    "ipAddress" INET,
    "userAgent" VARCHAR(500),
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "audit_logs_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "organisations_slug_key" ON "organisations"("slug");

-- CreateIndex
CREATE INDEX "organisations_deletedAt_idx" ON "organisations"("deletedAt");

-- CreateIndex
CREATE UNIQUE INDEX "profiles_email_key" ON "profiles"("email");

-- CreateIndex
CREATE INDEX "profiles_currentOrgId_idx" ON "profiles"("currentOrgId");

-- CreateIndex
CREATE UNIQUE INDEX "memberships_inviteTokenHash_key" ON "memberships"("inviteTokenHash");

-- CreateIndex
CREATE INDEX "memberships_profileId_status_idx" ON "memberships"("profileId", "status");

-- CreateIndex
CREATE INDEX "memberships_orgId_role_idx" ON "memberships"("orgId", "role");

-- CreateIndex
CREATE INDEX "memberships_invitedById_idx" ON "memberships"("invitedById");

-- CreateIndex
CREATE UNIQUE INDEX "memberships_orgId_profileId_key" ON "memberships"("orgId", "profileId");

-- CreateIndex
CREATE UNIQUE INDEX "memberships_orgId_invitedEmail_key" ON "memberships"("orgId", "invitedEmail");

-- CreateIndex
CREATE INDEX "sites_orgId_deletedAt_idx" ON "sites"("orgId", "deletedAt");

-- CreateIndex
CREATE UNIQUE INDEX "sites_orgId_name_key" ON "sites"("orgId", "name");

-- CreateIndex
CREATE INDEX "cameras_orgId_status_idx" ON "cameras"("orgId", "status");

-- CreateIndex
CREATE INDEX "cameras_siteId_idx" ON "cameras"("siteId");

-- CreateIndex
CREATE UNIQUE INDEX "cameras_orgId_name_key" ON "cameras"("orgId", "name");

-- CreateIndex
CREATE INDEX "zones_orgId_deletedAt_idx" ON "zones"("orgId", "deletedAt");

-- CreateIndex
CREATE INDEX "zones_cameraId_idx" ON "zones"("cameraId");

-- CreateIndex
CREATE INDEX "zones_siteId_idx" ON "zones"("siteId");

-- CreateIndex
CREATE INDEX "zones_createdById_idx" ON "zones"("createdById");

-- CreateIndex
CREATE UNIQUE INDEX "zones_cameraId_name_key" ON "zones"("cameraId", "name");

-- CreateIndex
CREATE INDEX "zone_minute_stats_orgId_bucketStart_idx" ON "zone_minute_stats"("orgId", "bucketStart");

-- CreateIndex
CREATE INDEX "zone_minute_stats_zoneId_bucketStart_idx" ON "zone_minute_stats"("zoneId", "bucketStart");

-- CreateIndex
CREATE INDEX "zone_minute_stats_siteId_bucketStart_idx" ON "zone_minute_stats"("siteId", "bucketStart");

-- CreateIndex
CREATE INDEX "zone_minute_stats_orgId_createdAt_idx" ON "zone_minute_stats"("orgId", "createdAt");

-- CreateIndex
CREATE INDEX "zone_minute_stats_cameraId_bucketStart_idx" ON "zone_minute_stats"("cameraId", "bucketStart");

-- CreateIndex
CREATE INDEX "zone_minute_stats_sessionId_idx" ON "zone_minute_stats"("sessionId");

-- CreateIndex
CREATE UNIQUE INDEX "zone_minute_stats_zoneId_bucketStart_key" ON "zone_minute_stats"("zoneId", "bucketStart");

-- CreateIndex
CREATE INDEX "zone_day_stats_orgId_statDate_idx" ON "zone_day_stats"("orgId", "statDate");

-- CreateIndex
CREATE INDEX "zone_day_stats_siteId_statDate_idx" ON "zone_day_stats"("siteId", "statDate");

-- CreateIndex
CREATE UNIQUE INDEX "zone_day_stats_zoneId_statDate_key" ON "zone_day_stats"("zoneId", "statDate");

-- CreateIndex
CREATE INDEX "analysis_sessions_orgId_queuedAt_idx" ON "analysis_sessions"("orgId", "queuedAt");

-- CreateIndex
CREATE INDEX "analysis_sessions_orgId_status_idx" ON "analysis_sessions"("orgId", "status");

-- CreateIndex
CREATE INDEX "analysis_sessions_cameraId_queuedAt_idx" ON "analysis_sessions"("cameraId", "queuedAt");

-- CreateIndex
CREATE INDEX "analysis_sessions_startedById_idx" ON "analysis_sessions"("startedById");

-- CreateIndex
CREATE INDEX "alert_rules_orgId_isEnabled_idx" ON "alert_rules"("orgId", "isEnabled");

-- CreateIndex
CREATE INDEX "alert_rules_zoneId_idx" ON "alert_rules"("zoneId");

-- CreateIndex
CREATE INDEX "alert_rules_cameraId_idx" ON "alert_rules"("cameraId");

-- CreateIndex
CREATE INDEX "alert_rules_createdById_idx" ON "alert_rules"("createdById");

-- CreateIndex
CREATE INDEX "alerts_orgId_triggeredAt_idx" ON "alerts"("orgId", "triggeredAt");

-- CreateIndex
CREATE INDEX "alerts_orgId_state_triggeredAt_idx" ON "alerts"("orgId", "state", "triggeredAt");

-- CreateIndex
CREATE INDEX "alerts_ruleId_triggeredAt_idx" ON "alerts"("ruleId", "triggeredAt");

-- CreateIndex
CREATE INDEX "alerts_zoneId_triggeredAt_idx" ON "alerts"("zoneId", "triggeredAt");

-- CreateIndex
CREATE INDEX "alerts_cameraId_idx" ON "alerts"("cameraId");

-- CreateIndex
CREATE INDEX "alerts_acknowledgedById_idx" ON "alerts"("acknowledgedById");

-- CreateIndex
CREATE INDEX "alerts_resolvedById_idx" ON "alerts"("resolvedById");

-- CreateIndex
CREATE UNIQUE INDEX "reports_downloadTokenHash_key" ON "reports"("downloadTokenHash");

-- CreateIndex
CREATE INDEX "reports_orgId_createdAt_idx" ON "reports"("orgId", "createdAt");

-- CreateIndex
CREATE INDEX "reports_status_expiresAt_idx" ON "reports"("status", "expiresAt");

-- CreateIndex
CREATE INDEX "reports_requestedById_idx" ON "reports"("requestedById");

-- CreateIndex
CREATE INDEX "audit_logs_orgId_createdAt_idx" ON "audit_logs"("orgId", "createdAt");

-- CreateIndex
CREATE INDEX "audit_logs_orgId_action_createdAt_idx" ON "audit_logs"("orgId", "action", "createdAt");

-- CreateIndex
CREATE INDEX "audit_logs_actorId_createdAt_idx" ON "audit_logs"("actorId", "createdAt");

-- AddForeignKey
ALTER TABLE "profiles" ADD CONSTRAINT "profiles_currentOrgId_fkey" FOREIGN KEY ("currentOrgId") REFERENCES "organisations"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "memberships" ADD CONSTRAINT "memberships_orgId_fkey" FOREIGN KEY ("orgId") REFERENCES "organisations"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "memberships" ADD CONSTRAINT "memberships_profileId_fkey" FOREIGN KEY ("profileId") REFERENCES "profiles"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "memberships" ADD CONSTRAINT "memberships_invitedById_fkey" FOREIGN KEY ("invitedById") REFERENCES "profiles"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "sites" ADD CONSTRAINT "sites_orgId_fkey" FOREIGN KEY ("orgId") REFERENCES "organisations"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "cameras" ADD CONSTRAINT "cameras_orgId_fkey" FOREIGN KEY ("orgId") REFERENCES "organisations"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "cameras" ADD CONSTRAINT "cameras_siteId_fkey" FOREIGN KEY ("siteId") REFERENCES "sites"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "zones" ADD CONSTRAINT "zones_orgId_fkey" FOREIGN KEY ("orgId") REFERENCES "organisations"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "zones" ADD CONSTRAINT "zones_cameraId_fkey" FOREIGN KEY ("cameraId") REFERENCES "cameras"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "zones" ADD CONSTRAINT "zones_siteId_fkey" FOREIGN KEY ("siteId") REFERENCES "sites"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "zones" ADD CONSTRAINT "zones_createdById_fkey" FOREIGN KEY ("createdById") REFERENCES "profiles"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "zone_minute_stats" ADD CONSTRAINT "zone_minute_stats_orgId_fkey" FOREIGN KEY ("orgId") REFERENCES "organisations"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "zone_minute_stats" ADD CONSTRAINT "zone_minute_stats_siteId_fkey" FOREIGN KEY ("siteId") REFERENCES "sites"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "zone_minute_stats" ADD CONSTRAINT "zone_minute_stats_cameraId_fkey" FOREIGN KEY ("cameraId") REFERENCES "cameras"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "zone_minute_stats" ADD CONSTRAINT "zone_minute_stats_zoneId_fkey" FOREIGN KEY ("zoneId") REFERENCES "zones"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "zone_minute_stats" ADD CONSTRAINT "zone_minute_stats_sessionId_fkey" FOREIGN KEY ("sessionId") REFERENCES "analysis_sessions"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "analysis_sessions" ADD CONSTRAINT "analysis_sessions_orgId_fkey" FOREIGN KEY ("orgId") REFERENCES "organisations"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "analysis_sessions" ADD CONSTRAINT "analysis_sessions_cameraId_fkey" FOREIGN KEY ("cameraId") REFERENCES "cameras"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "analysis_sessions" ADD CONSTRAINT "analysis_sessions_startedById_fkey" FOREIGN KEY ("startedById") REFERENCES "profiles"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "alert_rules" ADD CONSTRAINT "alert_rules_orgId_fkey" FOREIGN KEY ("orgId") REFERENCES "organisations"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "alert_rules" ADD CONSTRAINT "alert_rules_zoneId_fkey" FOREIGN KEY ("zoneId") REFERENCES "zones"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "alert_rules" ADD CONSTRAINT "alert_rules_cameraId_fkey" FOREIGN KEY ("cameraId") REFERENCES "cameras"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "alert_rules" ADD CONSTRAINT "alert_rules_createdById_fkey" FOREIGN KEY ("createdById") REFERENCES "profiles"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "alerts" ADD CONSTRAINT "alerts_orgId_fkey" FOREIGN KEY ("orgId") REFERENCES "organisations"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "alerts" ADD CONSTRAINT "alerts_ruleId_fkey" FOREIGN KEY ("ruleId") REFERENCES "alert_rules"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "alerts" ADD CONSTRAINT "alerts_zoneId_fkey" FOREIGN KEY ("zoneId") REFERENCES "zones"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "alerts" ADD CONSTRAINT "alerts_cameraId_fkey" FOREIGN KEY ("cameraId") REFERENCES "cameras"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "alerts" ADD CONSTRAINT "alerts_acknowledgedById_fkey" FOREIGN KEY ("acknowledgedById") REFERENCES "profiles"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "alerts" ADD CONSTRAINT "alerts_resolvedById_fkey" FOREIGN KEY ("resolvedById") REFERENCES "profiles"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "reports" ADD CONSTRAINT "reports_orgId_fkey" FOREIGN KEY ("orgId") REFERENCES "organisations"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "reports" ADD CONSTRAINT "reports_requestedById_fkey" FOREIGN KEY ("requestedById") REFERENCES "profiles"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "audit_logs" ADD CONSTRAINT "audit_logs_orgId_fkey" FOREIGN KEY ("orgId") REFERENCES "organisations"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "audit_logs" ADD CONSTRAINT "audit_logs_actorId_fkey" FOREIGN KEY ("actorId") REFERENCES "profiles"("id") ON DELETE SET NULL ON UPDATE CASCADE;
