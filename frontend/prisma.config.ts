// Prisma 7 configuration.
//
// Connection strings moved out of schema.prisma in Prisma 7 and live here.
// Note the shape changed too: `directUrl` no longer exists. The config
// datasource accepts exactly `url` and `shadowDatabaseUrl`.
//
// WHICH URL GOES WHERE (Supabase gives you two ports, and they are NOT
// interchangeable):
//
//   port 5432  A real Postgres session. `prisma migrate` needs this, because
//              migrations take advisory locks that a transaction-mode pooler
//              cannot hold. This is what `url` must point at here — the CLI
//              is the only consumer of this file.
//
//   port 6543  PgBouncer transaction pooler. Used by the running application
//              for query traffic, where connection churn matters. The
//              PrismaClient reads it from DATABASE_URL at runtime; it is not
//              configured here.
//
// So: DIRECT_URL (5432) for the CLI below, DATABASE_URL (6543) for the app.
// Neither carries a NEXT_PUBLIC_ prefix — both contain the database password
// and must never reach the browser.

// `import 'dotenv/config'` reads .env ONLY. Next.js keeps local secrets in
// .env.local, so it must be named explicitly or the CLI sees no DIRECT_URL.
// Both are loaded, .env.local first — dotenv does not overwrite an already-set
// variable, so .env.local wins and a real shell env var still beats both.
import { config as loadEnv } from 'dotenv';
loadEnv({ path: '.env.local' });
loadEnv();

import { defineConfig, env } from 'prisma/config';

export default defineConfig({
  schema: 'prisma/schema.prisma',

  migrations: {
    path: 'prisma/migrations',
    seed: 'npx tsx prisma/seed.ts',
  },

  datasource: {
    // Migrations run over the direct connection.
    url: env('DIRECT_URL'),
  },
});
