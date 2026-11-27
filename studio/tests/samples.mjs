// Sample mappings test: serves the studio with scripts/serve.mjs, loads every mapping listed in samples/catalog.json
// through the "From the samples folder" menu, runs it with real DuckDB-WASM and compares each file it writes with
// samples/target. Pass --write to (re)generate samples/target from the current run instead of comparing.
//
//   npm ci && npx playwright install chromium && npm run test:samples
//
// DATAFORGE_TEST_MIRROR works as in tests/smoke.mjs for offline runs.
import { chromium } from 'playwright';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, extname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createDataForgeServer } from '../scripts/serve.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const write = process.argv.includes('--write');
const mirror = process.env.DATAFORGE_TEST_MIRROR;
const catalog = JSON.parse(readFileSync(join(root, 'samples/catalog.json'), 'utf8')).samples;

const server = createDataForgeServer({ root });
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const base = `http://127.0.0.1:${server.address().port}/`;
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
if (mirror) await context.route('https://cdn.jsdelivr.net/**', (route) => {
  const rest = /duckdb-wasm@[^/]+\/(.*)$/.exec(route.request().url())?.[1];
  const file = rest === '+esm' ? join(mirror, 'esm.js') : rest ? join(mirror, rest) : null;
  if (!file || !existsSync(file)) return route.abort();
  return route.fulfill({ body: readFileSync(file), contentType: extname(file) === '.wasm' ? 'application/wasm' : 'text/javascript', headers: { 'access-control-allow-origin': '*' } });
});

const problems = [], results = [];
const page = await context.newPage();
page.setDefaultTimeout(15000);
page.on('pageerror', (e) => problems.push('page error: ' + e.message));
page.on('console', (m) => { if (/Content Security Policy|Refused to/i.test(m.text())) problems.push('CSP: ' + m.text().slice(0, 200)); });
page.on('dialog', (d) => { problems.push('native dialog: ' + d.message().slice(0, 80)); d.dismiss(); });
const ready = () => page.waitForFunction(() => window.DataForge?.Repo.project && document.querySelector('#enginePill').dataset.state !== 'loading', null, { timeout: 120000 });
const idle = () => page.waitForFunction(() => !DataForge.state.running, null, { timeout: 60000 });
async function check(name, fn) {
  try { const detail = await fn(); results.push(['ok', name]); console.log('  ok   ' + name + (detail ? '  · ' + detail : '')); }
  catch (e) { results.push(['FAIL', name]); console.log('  FAIL ' + name + '\n       ' + String(e.message).split('\n').join('\n       ')); }
}
const expect = (cond, message) => { if (!cond) throw new Error(message); };
// Writes from a target node, keyed by its file name.
const outputs = () => page.evaluate(async () => {
  const D = DataForge, out = {};
  for (const n of D.state.doc.nodes) {
    const o = D.state.outputs[n.id];
    if (o && n.config.fileName) out[n.config.fileName] = await (await fetch(o.url)).text();
  }
  return out;
});
const lines = (text) => text.replace(/\r\n/g, '\n').split('\n').filter(Boolean);

await page.goto(base);
await ready();
await page.waitForTimeout(400);

for (const sample of catalog) {
  await check(`sample "${sample.id}" loads from samples/ and runs`, async () => {
    await page.click('#btnSampleMenu');
    await page.click(`[data-repo-sample="${sample.mapping}"]`);
    await page.waitForFunction((file) => DataForge.state.doc.nodes.length && DataForge.state.doc.nodes
      .filter((n) => DataForge.Registry.get(n.type).category === 'source').every((n) => DataForge.Files.has(n.id)), sample.mapping);
    await page.waitForTimeout(300);
    const errors = await page.evaluate(async () => (await DataForge.Validator.run()).filter((i) => i.severity === 'error').map((i) => i.message));
    expect(!errors.length, 'validation errors: ' + errors.join('; '));
    await page.click('#btnRun'); await page.waitForTimeout(300); await idle(); await page.waitForTimeout(200);
    const s = await page.evaluate(() => { const x = DataForge.state.log.findLast((l) => l.kind === 'summary'); return x ? { ok: x.ok, rows: x.rows, rejected: x.rejected } : null; });
    expect(s?.ok, 'run failed: ' + JSON.stringify(await page.evaluate(() => DataForge.state.log.filter((l) => l.kind !== 'summary').slice(-3))));
    const files = await outputs();
    const targets = await page.evaluate(() => DataForge.state.doc.nodes.filter((n) => ['target'].includes(DataForge.Registry.get(n.type).category) && n.config.fileName).map((n) => n.config.fileName));
    expect(targets.length && targets.every((t) => t in files), `missing output for ${targets.filter((t) => !(t in files)).join(', ')}`);
    for (const [name, text] of Object.entries(files)) {
      const path = join(root, 'samples/target', name);
      if (write) { writeFileSync(path, text); continue; }
      expect(existsSync(path), `samples/target/${name} does not exist (run with --write)`);
      const want = readFileSync(path, 'utf8');
      if (text === want) continue;
      const a = lines(text), b = lines(want);
      // Reject files have no sort step; accept the same rows in another order.
      const sameRows = a.length === b.length && a[0] === b[0] && JSON.stringify(a.slice(1).sort()) === JSON.stringify(b.slice(1).sort());
      expect(sameRows, `samples/target/${name} differs:\n  got      ${a.find((l, i) => l !== b[i])}\n  expected ${b.find((l, i) => l !== a[i])}`);
    }
    return `${s.rows} rows, ${s.rejected} rejected · ${write ? 'wrote' : 'matched'} ${Object.keys(files).join(', ')}`;
  });
}

await check('loaded samples open as separate repository mappings', async () => {
  const names = await page.evaluate(() => DataForge.Repo.project.mappings.map((m) => m.mapping.name));
  for (const s of catalog) {
    const doc = JSON.parse(readFileSync(join(root, 'samples/mappings', s.mapping), 'utf8'));
    expect(names.includes(doc.mapping.name), `"${doc.mapping.name}" is not in the repository: ${JSON.stringify(names)}`);
  }
  return names.length + ' mappings';
});
await check('a loaded sample still runs after a reload (source files remembered)', async () => {
  await page.reload(); await ready(); await page.waitForTimeout(400);
  const first = JSON.parse(readFileSync(join(root, 'samples/mappings', catalog[0].mapping), 'utf8')).mapping.name;
  await page.evaluate(async (name) => { const D = DataForge, m = D.Repo.project.mappings.find((x) => x.mapping.name === name); await D.Repo.openMapping(m.mapping.id); }, first);
  await page.waitForTimeout(500);
  await page.click('#btnRun'); await page.waitForTimeout(300); await idle(); await page.waitForTimeout(200);
  const s = await page.evaluate(() => DataForge.state.log.findLast((l) => l.kind === 'summary'));
  expect(s?.ok, 'run after reload failed');
  return `${s.rows} rows`;
});
await check('no page errors, CSP violations or native dialogs', async () => { expect(!problems.length, problems.join('\n')); });

await browser.close();
server.closeAllConnections(); server.close();
const failed = results.filter((r) => r[0] === 'FAIL').length;
console.log(`\n${results.length - failed}/${results.length} sample checks passed${write ? ' (targets written)' : ''}.`);
process.exit(failed ? 1 : 0);
