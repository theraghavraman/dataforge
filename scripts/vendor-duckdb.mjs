#!/usr/bin/env node
// Self-host DuckDB-WASM instead of loading it from the jsDelivr CDN.
//
//   node scripts/vendor-duckdb.mjs          download into vendor/duckdb/ and point index.html at it
//   node scripts/vendor-duckdb.mjs --cdn    point index.html back at the CDN (vendor/ is left in place)
//
// The release is read from APP.duckdb in index.html, fetched from the npm registry (npm verifies the
// package integrity), and its ES module is bundled with its only dependency (apache-arrow) into one file.
// Needs Node 18+ and npm. Nothing is installed into this repository.
//
// Note: JSON sources and targets still download DuckDB's JSON extension from extensions.duckdb.org on first use.
import { execFileSync } from 'node:child_process';
import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const indexPath = join(root, 'index.html');
const META = /(<meta name="dataforge-duckdb" content=")[^"]*(">)/;

function setSource(value) {
  const html = readFileSync(indexPath, 'utf8');
  if (!META.test(html)) throw new Error('index.html has no <meta name="dataforge-duckdb"> tag.');
  writeFileSync(indexPath, html.replace(META, `$1${value}$2`));
}

if (process.argv.includes('--cdn')) {
  setSource('');
  console.log('index.html now loads DuckDB-WASM from the CDN.');
  process.exit(0);
}

const version = /duckdb: '([^']+)'/.exec(readFileSync(indexPath, 'utf8'))?.[1];
if (!version) throw new Error('Could not read APP.duckdb from index.html.');
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';
const run = (args, cwd) => execFileSync(npm, args, { cwd, stdio: ['ignore', 'pipe', 'inherit'], shell: process.platform === 'win32' }).toString();
const work = mkdtempSync(join(tmpdir(), 'dataforge-duckdb-'));
const out = join(root, 'vendor', 'duckdb');

try {
  console.log(`Fetching @duckdb/duckdb-wasm@${version} ...`);
  writeFileSync(join(work, 'package.json'), '{"private":true}');
  run(['install', '--no-audit', '--no-fund', '--silent', `@duckdb/duckdb-wasm@${version}`, 'esbuild@0.25'], work);
  const dist = join(work, 'node_modules', '@duckdb', 'duckdb-wasm', 'dist');

  console.log('Bundling the ES module ...');
  writeFileSync(join(work, 'entry.js'), 'export * from "@duckdb/duckdb-wasm";\n');
  writeFileSync(join(work, 'build.mjs'), "import { build } from 'esbuild';\nawait build({ entryPoints: ['entry.js'], bundle: true, format: 'esm', minify: true, target: 'es2020', logLevel: 'error', outfile: 'duckdb.mjs' });\n");
  execFileSync(process.execPath, ['build.mjs'], { cwd: work, stdio: 'inherit' });

  rmSync(out, { recursive: true, force: true });
  mkdirSync(out, { recursive: true });
  const files = ['duckdb-mvp.wasm', 'duckdb-eh.wasm', 'duckdb-browser-mvp.worker.js', 'duckdb-browser-eh.worker.js'];
  for (const f of files) cpSync(join(dist, f), join(out, f));
  cpSync(join(work, 'duckdb.mjs'), join(out, 'duckdb.mjs'));
  writeFileSync(join(out, 'VERSION'), version + '\n');
  setSource('vendor/duckdb/');

  const mb = (f) => (statSync(join(out, f)).size / 1048576).toFixed(1) + ' MB';
  console.log(`\nvendor/duckdb/ (DuckDB-WASM ${version})`);
  for (const f of ['duckdb.mjs', ...files]) console.log(`  ${f.padEnd(32)} ${mb(f)}`);
  console.log('\nindex.html now loads DuckDB-WASM from vendor/duckdb/ and falls back to the CDN.');
  console.log('Commit vendor/duckdb/ and serve the folder over http(s); .wasm must be served as application/wasm.');
} finally {
  rmSync(work, { recursive: true, force: true });
}
