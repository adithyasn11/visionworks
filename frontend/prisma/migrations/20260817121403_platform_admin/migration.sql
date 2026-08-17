-- CreateTable
CREATE TABLE "platform_admins" (
    "profileId" UUID NOT NULL,
    "note" VARCHAR(200),
    "grantedById" UUID,
    "grantedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "revokedAt" TIMESTAMPTZ(6),

    CONSTRAINT "platform_admins_pkey" PRIMARY KEY ("profileId")
);

-- CreateTable
CREATE TABLE "platform_audit_logs" (
    "id" UUID NOT NULL,
    "actorId" UUID,
    "actorEmail" VARCHAR(320),
    "action" VARCHAR(80) NOT NULL,
    "targetOrgId" UUID,
    "targetOrgName" VARCHAR(160),
    "metadata" JSONB,
    "ipAddress" INET,
    "userAgent" VARCHAR(500),
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "platform_audit_logs_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "platform_admins_revokedAt_idx" ON "platform_admins"("revokedAt");

-- CreateIndex
CREATE INDEX "platform_admins_grantedById_idx" ON "platform_admins"("grantedById");

-- CreateIndex
CREATE INDEX "platform_audit_logs_createdAt_idx" ON "platform_audit_logs"("createdAt");

-- CreateIndex
CREATE INDEX "platform_audit_logs_actorId_createdAt_idx" ON "platform_audit_logs"("actorId", "createdAt");

-- CreateIndex
CREATE INDEX "platform_audit_logs_targetOrgId_createdAt_idx" ON "platform_audit_logs"("targetOrgId", "createdAt");

-- AddForeignKey
ALTER TABLE "platform_admins" ADD CONSTRAINT "platform_admins_profileId_fkey" FOREIGN KEY ("profileId") REFERENCES "profiles"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "platform_admins" ADD CONSTRAINT "platform_admins_grantedById_fkey" FOREIGN KEY ("grantedById") REFERENCES "profiles"("id") ON DELETE SET NULL ON UPDATE CASCADE;
