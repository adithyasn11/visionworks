/**
 * Read-only database inspector.
 *
 * Lists every table in `public` with its row count, so you can see what is
 * actually in the database before running anything destructive.
 *
 *   npx tsx prisma/inspect.ts
 *
 * Touches nothing. Safe to run against production.
 */

import { config as loadEnv } from 'dotenv';
loadEnv({ path: '.env.local' });
loadEnv();

import { PrismaPg } from '@prisma/adapter-pg';
import { PrismaClient } from '../app/generated/prisma/client';

const connectionString = process.env.DIRECT_URL ?? process.env.DATABASE_URL;
if (!connectionString) {
  console.error('Set DIRECT_URL or DATABASE_URL in .env.local');
  process.exit(1);
}

const prisma = new PrismaClient({ adapter: new PrismaPg({ connectionString }) });

async function main() {
  const tables = await prisma.$queryRawUnsafe<{ table_name: string }[]>(`
    SELECT table_name FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    ORDER BY table_name
  `);

  if (tables.length === 0) {
    console.log('\npublic schema is empty.\n');
    return;
  }

  console.log(`\n${tables.length} table(s) in public:\n`);
  let total = 0n;
  for (const t of tables) {
    const r = await prisma.$queryRawUnsafe<{ c: bigint }[]>(
      `SELECT count(*)::bigint AS c FROM public."${t.table_name}"`,
    );
    total += r[0].c;
    console.log(`  ${t.table_name.padEnd(26)} ${String(r[0].c).padStart(12)}`);
  }
  console.log(`\n  ${'TOTAL'.padEnd(26)} ${String(total).padStart(12)}\n`);
}

main()
  .catch((e) => {
    console.error('Inspect failed:', e instanceof Error ? e.message : e);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
