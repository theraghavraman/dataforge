// Step 1 of the generated-code check: load each bundled sample in the real app (headless Chromium), run it,
// and write to <out>/<sample>/ the code DataForge generates for every dialect, the sample's source files,
// and the files the app itself produced. verify.py then runs that code in its own engine and compares.
//
//   node tests/codegen/export.mjs <out-dir>        (DATAFORGE_TEST_MIRROR works as in tests/smoke.mjs)
import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { existsSync, readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, extname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const outRoot = resolve(process.argv[2] || 'codegen-out'), mirror = process.env.DATAFORGE_TEST_MIRROR;
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.wasm': 'application/wasm' };
const server = createServer((req, res) => {
  const file = join(root, decodeURIComponent(new URL(req.url, 'http://x').pathname).replace(/^\/+/, '') || 'index.html');
  if (!file.startsWith(root) || !existsSync(file)) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { 'content-type': TYPES[extname(file)] || 'application/octet-stream' }); res.end(readFileSync(file));
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
if (mirror) await context.route('https://cdn.jsdelivr.net/**', (route) => {
  const rest = /duckdb-wasm@[^/]+\/(.*)$/.exec(route.request().url())?.[1];
  const file = rest === '+esm' ? join(mirror, 'esm.js') : rest ? join(mirror, rest) : null;
  if (!file || !existsSync(file)) return route.abort();
  return route.fulfill({ body: readFileSync(file), contentType: TYPES[extname(file)] || 'text/javascript', headers: { 'access-control-allow-origin': '*' } });
});
const page = await context.newPage();
page.on('pageerror', (e) => { console.error('page error: ' + e.message); process.exitCode = 1; });
await page.goto(`http://localhost:${server.address().port}/`);
await page.waitForFunction(() => window.DataForge?.Repo.project && document.querySelector('#enginePill').dataset.state === 'ready', null, { timeout: 120000 });
await page.click('#generalModal >> text=Continue').catch(() => {});

for (const sample of ['original', 'phase2', 'phase3']) {
  await page.click('#btnSampleMenu'); await page.click(`[data-sample="${sample}"]`); await page.waitForTimeout(600);
  await page.click('#btnRun'); await page.waitForTimeout(300);
  await page.waitForFunction(() => !DataForge.state.running, null, { timeout: 60000 }); await page.waitForTimeout(300);
  const dump = await page.evaluate(async () => {
    const D = DataForge, b64 = (u8) => { let s = ''; for (let i = 0; i < u8.length; i += 8192) s += String.fromCharCode(...u8.subarray(i, i + 8192)); return btoa(s); };
    const code = {};
    for (const d of ['snowflake', 'pyspark', 'dbt', 'duckdb']) { const g = D.CodeGen.generate(d); code[d] = { text: g.text || null, files: g.files || null }; }
    const sources = [], outputs = [];
    for (const n of D.state.doc.nodes) {
      const f = D.Files.map[n.id], o = D.state.outputs[n.id];
      if (f) sources.push({ fileName: f.fileName, b64: b64(f.bytes) });
      if (o) outputs.push({ node: n.name, b64: b64(new Uint8Array(await (await fetch(o.url)).arrayBuffer())) });
    }
    const s = D.state.log.findLast((x) => x.kind === 'summary');
    return { mapping: D.state.doc, code, sources, outputs, ok: !!s?.ok };
  });
  if (!dump.ok) throw new Error(`sample "${sample}" did not run in the app`);
  const put = (rel, data) => { const f = join(outRoot, sample, rel); mkdirSync(dirname(f), { recursive: true }); writeFileSync(f, data); };
  put('mapping.json', JSON.stringify(dump.mapping, null, 1));
  for (const s of dump.sources) put('data/' + s.fileName, Buffer.from(s.b64, 'base64'));
  for (const o of dump.outputs) put('app_out/' + o.node + '.csv', Buffer.from(o.b64, 'base64'));
  put('duckdb.sql', dump.code.duckdb.text); put('snowflake.sql', dump.code.snowflake.text); put('etl_job.py', dump.code.pyspark.text);
  for (const [name, body] of Object.entries(dump.code.dbt.files)) put('dbt/' + name, body);
  console.log(`exported ${sample}: ${dump.sources.length} source file(s), ${dump.outputs.length} target file(s)`);
}
await browser.close(); server.close();
