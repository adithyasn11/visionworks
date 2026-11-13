// frontend/scripts/sql.mjs
// Runs SQL against the project database (DIRECT_URL in .env.local) and prints the rows.
//   node scripts/sql.mjs "select 1"          one statement
//   node scripts/sql.mjs -f prisma/sql/029_x.sql   a whole file, in one transaction
import fs from 'node:fs';
import pg from 'pg';

const env = Object.fromEntries(
  fs.readFileSync(new URL('../.env.local', import.meta.url), 'utf8').split(/\r?\n/)
    .filter((l) => /^[A-Z_]+=/.test(l)).map((l) => { const i = l.indexOf('='); return [l.slice(0, i), l.slice(i + 1).replace(/^"|"$/g, '')]; }));
const url = env.DIRECT_URL || env.DATABASE_URL;
const args = process.argv.slice(2);
const sql = args[0] === '-f' ? fs.readFileSync(args[1], 'utf8') : args.join(' ');
const client = new pg.Client({ connectionString: url, ssl: { rejectUnauthorized: false } });
await client.connect();
try {
  if (args[0] === '-f') { await client.query('BEGIN'); await client.query(sql); await client.query('COMMIT'); console.log('applied', args[1]); }
  else {
    const res = await client.query(sql);
    if (process.env.RAW) { for (const row of res.rows) console.log(Object.values(row).join('	')); }
    else for (const r of [].concat(res)) if (r.rows?.length) console.table(r.rows); else console.log(r.command, r.rowCount ?? '');
  }
} catch (e) {
  if (args[0] === '-f') await client.query('ROLLBACK').catch(() => {});
  console.error('ERROR:', e.message); process.exitCode = 1;
} finally { await client.end(); }
