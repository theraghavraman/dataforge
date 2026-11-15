// End-to-end smoke test: drives the real app in headless Chromium and runs real DuckDB-WASM queries.
//
//   npm install && npx playwright install chromium && npm test
//
// DuckDB-WASM is loaded the same way a visitor loads it (self-hosted copy if configured, otherwise the CDN).
// For offline runs, set DATAFORGE_TEST_MIRROR to a folder that holds "esm.js" (a bundled ES module build of
// @duckdb/duckdb-wasm) and "dist/" (the package's dist folder); CDN requests are then answered from it.
import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { existsSync, readFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, extname, join, normalize } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const mirror = process.env.DATAFORGE_TEST_MIRROR;
const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.wasm': 'application/wasm', '.json': 'application/json' };

const server = createServer((req, res) => {
  const path = normalize(decodeURIComponent(new URL(req.url, 'http://x').pathname)).replace(/^([/\\])+/, '');
  const file = join(root, path || 'index.html');
  if (!file.startsWith(root) || !existsSync(file)) { res.writeHead(404); res.end('not found'); return; }
  res.writeHead(200, { 'content-type': TYPES[extname(file)] || 'application/octet-stream' });
  res.end(readFileSync(file));
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const base = `http://localhost:${server.address().port}/`;

const results = [], problems = [];
const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
await context.grantPermissions(['clipboard-read', 'clipboard-write']);
if (mirror) await context.route('https://cdn.jsdelivr.net/**', (route) => {
  const rest = /duckdb-wasm@[^/]+\/(.*)$/.exec(route.request().url())?.[1];
  const file = rest === '+esm' ? join(mirror, 'esm.js') : rest ? join(mirror, rest) : null;
  if (!file || !existsSync(file)) return route.abort();
  return route.fulfill({ body: readFileSync(file), contentType: TYPES[extname(file)] || 'text/javascript', headers: { 'access-control-allow-origin': '*' } });
});

function watch(page) {
  page.on('pageerror', (e) => problems.push('page error: ' + e.message));
  page.on('console', (m) => { if (/Content Security Policy|Refused to/i.test(m.text())) problems.push('CSP: ' + m.text().slice(0, 200)); });
  page.on('dialog', (d) => { problems.push('native dialog: ' + d.message().slice(0, 80)); d.dismiss(); });
}
async function open(url = base) {
  const page = await context.newPage(); watch(page); page.setDefaultTimeout(10000);
  await page.goto(url);
  await page.waitForFunction(() => window.DataForge?.Repo.project && document.querySelector('#enginePill').dataset.state !== 'loading', null, { timeout: 120000 });
  await page.waitForTimeout(400);
  return page;
}
async function check(name, fn) {
  try { const detail = await fn(); results.push(['ok', name, detail]); console.log('  ok   ' + name + (detail ? '  · ' + detail : '')); }
  catch (e) { results.push(['FAIL', name, e.message]); console.log('  FAIL ' + name + '\n       ' + String(e.message).split('\n')[0]); }
}
const expect = (cond, message) => { if (!cond) throw new Error(message); };
const idle = (page) => page.waitForFunction(() => !DataForge.state.running && !DataForge.Workflow.runState, null, { timeout: 60000 });
const summary = (page) => page.evaluate(() => { const s = DataForge.state.log.findLast((x) => x.kind === 'summary'); return s ? { ok: s.ok, rows: s.rows, rejected: s.rejected } : null; });
const run = async (page) => { await page.click('#btnRun'); await page.waitForTimeout(300); await idle(page); await page.waitForTimeout(200); };
const dialog = async (page, label) => { await page.waitForSelector('#dialogOverlay:not([hidden])'); await page.click(`#dialogBox button:text-is("${label}")`); await page.waitForTimeout(250); };

let page = await open();

await check('engine starts', async () => {
  const state = await page.getAttribute('#enginePill', 'data-state');
  expect(state === 'ready', 'engine state is ' + state + ': ' + await page.textContent('#enginePill'));
  return (await page.textContent('#engineMsg')).trim();
});
await check('first run: welcome screen and about dialog', async () => {
  expect(await page.isVisible('#welcomeStage'), 'welcome screen missing');
  await page.click('#generalModal >> text=Continue');
  await page.click('#btnSampleMenu'); await page.click('[data-sample="original"]'); await page.waitForTimeout(500);
  expect(!(await page.isVisible('#welcomeStage')), 'welcome screen still covers the canvas after loading a sample');
});

const EXPECTED = { original: { rows: 120, rejected: 0 }, phase2: { rows: 41, rejected: 120 }, phase3: { rows: 46, rejected: 2 } };
for (const [sample, want] of Object.entries(EXPECTED)) {
  await check(`sample "${sample}" runs`, async () => {
    if (sample !== 'original') { await page.click('#btnSampleMenu'); await page.click(`[data-sample="${sample}"]`); await page.waitForTimeout(500); }
    await run(page);
    const s = await summary(page);
    expect(s?.ok && s.rows === want.rows && s.rejected === want.rejected, 'unexpected result ' + JSON.stringify(s));
    return `${s.rows} rows, ${s.rejected} rejected`;
  });
  await check(`sample "${sample}" exports to every dialect`, async () => {
    const out = await page.evaluate(() => ['snowflake', 'pyspark', 'dbt', 'duckdb'].map((d) => { try { const g = DataForge.CodeGen.generate(d); return [d, (g.files ? Object.values(g.files).join('') : g.text).length]; } catch (e) { return [d, 'error: ' + e.message]; } }));
    for (const [d, size] of out) expect(typeof size === 'number' && size > 200, `${d}: ${size}`);
    const sql = await page.evaluate(() => DataForge.CodeGen.generate('duckdb').text);
    expect(!/\$\$[A-Za-z_]/.test(sql.replace(/^--.*$/gm, '')), 'DuckDB export still contains $$ parameters');
  });
}
await check('loading a sample never overwrites an existing mapping', async () => {
  const n = await page.evaluate(() => DataForge.Repo.project.mappings.length);
  expect(n === 3, 'expected 3 mappings, found ' + n);
});
await check('drawer tabs, export dialog and lineage render', async () => {
  for (const t of ['preview', 'sql', 'validation', 'log', 'dq', 'lineage']) await page.click(`.tab[data-tab="${t}"]`);
  await page.click('#btnExportCode'); await page.waitForTimeout(200);
  expect(!/^null$/m.test(await page.innerText('#codegenWarnings')), 'stray "null" in export warnings');
  await page.keyboard.press('Tab'); expect(await page.evaluate(() => document.querySelector('#codegenOverlay').contains(document.activeElement)), 'focus left the export dialog');
  await page.click('#codegenClose');
});
await check('save version through the in-app dialog', async () => {
  await page.keyboard.press('Control+Shift+S'); await page.waitForSelector('#dialogBox input'); await page.keyboard.type('baseline'); await page.keyboard.press('Enter'); await page.waitForTimeout(300);
  const labels = await page.evaluate(() => Object.values(DataForge.Repo.project.versions).flat().map((v) => v.label));
  expect(labels.includes('baseline'), 'version not saved: ' + JSON.stringify(labels));
});
await check('delete dialog: cancel keeps, confirm deletes, Delete key does not reach the canvas', async () => {
  await page.evaluate(() => DataForge.Actions.select(DataForge.state.doc.nodes.map((n) => n.id)));
  const nodes = await page.evaluate(() => DataForge.state.doc.nodes.length);
  await page.click('.repo-item .repo-more >> nth=0'); await page.click('#ctxMenu button:has-text("Delete")'); await page.waitForSelector('#dialogOverlay:not([hidden])');
  await page.keyboard.press('Delete'); await page.keyboard.press('Escape'); await page.waitForTimeout(200);
  expect(await page.evaluate(() => DataForge.Repo.project.mappings.length) === 3, 'cancel deleted the mapping');
  expect(await page.evaluate(() => DataForge.state.doc.nodes.length) === nodes, 'Delete key removed nodes behind the dialog');
  await page.click('.repo-item .repo-more >> nth=0'); await page.click('#ctxMenu button:has-text("Delete")'); await dialog(page, 'Delete');
  expect(await page.evaluate(() => DataForge.Repo.project.mappings.length) === 2, 'confirm did not delete');
});

await check('workflow: new, sample, run (button and Ctrl+Enter), row counter resets', async () => {
  await page.click('#repoBody button:text-is("+ Workflow")'); await page.waitForTimeout(300);
  expect(await page.evaluate(() => DataForge.Studio.mode) === 'workflow', '+ Workflow did not open the workflow designer');
  await page.click('#btnNew'); await page.waitForTimeout(200);
  expect(await page.evaluate(() => DataForge.Repo.project.workflows.length) === 2, 'New did not create a workflow');
  await page.evaluate(() => DataForge.Workflow.sample()); await page.waitForTimeout(300);
  await run(page);
  await page.click('#canvas', { position: { x: 30, y: 300 } }); await page.keyboard.press('Control+Enter'); await page.waitForTimeout(300); await idle(page); await page.waitForTimeout(300);
  const runs = await page.evaluate(() => DataForge.Repo.runs.filter((r) => r.type === 'workflow').map((r) => [r.status, r.rows, r.parameters.$$WF_ROWS]));
  expect(runs.length === 2 && runs.every((r) => r[0] === 'SUCCEEDED' && r[1] === 207 && r[2] === '207'), 'unexpected workflow runs ' + JSON.stringify(runs));
  return '2 runs, 207 rows each';
});
await check('workflow run blocks switching to a mapping', async () => {
  await page.click('#btnRun'); await page.waitForFunction(() => !!DataForge.Workflow.runState);
  await page.selectOption('#studioMode', 'mapping');
  expect(await page.evaluate(() => DataForge.Studio.mode) === 'workflow', 'mode switched during a run');
  await idle(page);
});
let bundle;
await check('workflow export and project import (cancel / merge / replace with backup)', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'dataforge-test-'));
  const [dl] = await Promise.all([page.waitForEvent('download'), page.keyboard.press('Control+s')]);
  bundle = join(dir, dl.suggestedFilename()); await dl.saveAs(bundle);
  const doc = JSON.parse(readFileSync(bundle, 'utf8'));
  expect(doc.workflows.length === 1 && doc.mappings.length === 3, 'bundle should hold 1 workflow and its 3 mappings');
  const counts = () => page.evaluate(() => DataForge.Repo.project.mappings.length + '/' + DataForge.Repo.project.workflows.length);
  const before = await counts();
  await page.setInputFiles('#openInput', bundle); await dialog(page, 'Cancel'); expect(await counts() === before, 'cancel changed the project');
  await page.setInputFiles('#openInput', bundle); await dialog(page, 'Merge'); await page.waitForTimeout(400);
  const merged = await counts(); expect(merged !== before, 'merge added nothing');
  const backup = page.waitForEvent('download');
  await page.setInputFiles('#projectInput', bundle); await dialog(page, 'Replace current project');
  expect(/backup/.test((await backup).suggestedFilename()), 'no backup downloaded before replace');
  await page.waitForTimeout(400); expect(await counts() === '3/1', 'replace result ' + await counts());
  return `${before} → merge ${merged} → replace 3/1`;
});
await check('monitor, XML import, diagram export, command palette', async () => {
  await page.selectOption('#studioMode', 'monitor'); await page.waitForTimeout(300);
  expect((await page.locator('.monitor-table tbody tr').count()) > 0, 'monitor shows no runs');
  await page.evaluate(() => DataForge.XmlImport.demo()); await page.waitForTimeout(600);
  expect(await page.evaluate(() => DataForge.Studio.mode === 'mapping' && DataForge.state.doc.nodes.length === 5), 'XML import did not open a 5-node mapping');
  await page.keyboard.press('Escape');
  const [png] = await Promise.all([page.waitForEvent('download'), page.click('#repoBody button:text-is("PNG")')]);
  expect(/\.png$/.test(png.suggestedFilename()), 'PNG export failed');
  await page.keyboard.press('Control+k'); await page.keyboard.type('open'); await page.keyboard.press('ArrowDown'); await page.keyboard.press('Enter'); await page.waitForTimeout(200);
  expect(await page.evaluate(() => DataForge.Studio.mode) !== 'mapping', 'command palette did not run the selected command');
});

await check('reload: project, last mapping and sample data survive; edits made just before closing are kept', async () => {
  const id = await page.evaluate(() => { const R = DataForge.Repo, m = R.project.mappings.find((x) => x.mapping.sample === 'phase3') || R.project.mappings[0]; R.openMapping(m.mapping.id); return m.mapping.id; });
  await page.waitForTimeout(400);
  await page.evaluate(() => { DataForge.state.doc.mapping.name = 'Renamed before close'; DataForge.History.commit(); window.dispatchEvent(new Event('pagehide')); });
  await page.waitForTimeout(200); await page.close(); page = await open();
  const now = await page.evaluate(() => ({ id: DataForge.state.doc.mapping.id, name: DataForge.state.doc.mapping.name, about: !document.querySelector('#generalModal').hidden }));
  expect(now.id === id && now.name === 'Renamed before close', 'reopened ' + JSON.stringify(now));
  expect(!now.about, 'about dialog shown again');
  await run(page); const s = await summary(page);
  expect(s?.ok && s.rows > 0, 'run after reload: ' + JSON.stringify(s));
});
await check('uploaded CSV: run, remember on this device, reload, run again', async () => {
  await page.click('#btnNew'); await page.waitForTimeout(300);
  await page.evaluate(async () => {
    const { Actions: A, state, History } = DataForge;
    A.addNode('csv_source', 60, 100); A.addNode('filter', 340, 100); A.addNode('csv_target', 620, 100);
    const [src, fil, tgt] = state.doc.nodes; A.link(src.id, fil.id); A.link(fil.id, tgt.id);
    src.config.rememberFile = true; fil.config.condition = 'qty > 1'; History.commit();
    await A.attachFile(src.id, new File(['id,name,qty\n1,"O\'Brien, ""Al""",2\n2,<b>x</b>,1\n3,ünïcode,5\n'], 'my data (1).csv', { type: 'text/csv' }));
  });
  await page.waitForTimeout(800); await run(page);
  let s = await summary(page); expect(s?.ok && s.rows === 2, 'first run ' + JSON.stringify(s));
  await page.waitForTimeout(400); await page.close(); page = await open();
  await run(page); s = await summary(page); expect(s?.ok && s.rows === 2, 'run after reload ' + JSON.stringify(s));
});
// JSON sources and targets need DuckDB's JSON extension, which the engine downloads from extensions.duckdb.org on first use.
// An offline run (DATAFORGE_TEST_MIRROR) cannot reach that host, so these checks only run online.
const jsonMapping = (fileName, text, sourceFormat, targetFormat) => page.evaluate(async ([fileName, text, sourceFormat, targetFormat]) => {
  const { Actions: A, state, History } = DataForge;
  A.addNode('json_source', 60, 100); A.addNode('filter', 340, 100); A.addNode('json_target', 620, 100);
  const [src, fil, tgt] = state.doc.nodes; A.link(src.id, fil.id); A.link(fil.id, tgt.id);
  src.config.format = sourceFormat; fil.config.condition = 'qty > 1'; tgt.config.format = targetFormat; tgt.config.fileName = 'out.json'; History.commit();
  await A.attachFile(src.id, new File([text], fileName, { type: 'application/json' }));
  return tgt.id;
}, [fileName, text, sourceFormat, targetFormat]);
const targetText = (id) => page.evaluate(async (id) => { const o = DataForge.state.outputs[id]; return o ? (await fetch(o.url)).text() : null; }, id);
const ROWS = [{ id: 1, name: 'O\'Brien, "Al"', qty: 2, tags: ['a', 'b'] }, { id: 2, name: '<b>x</b>', qty: 1, tags: [] }, { id: 3, name: 'ünïcode', qty: 5, tags: ['c'] }];
if (mirror) console.log('  skip JSON source and target (offline run: the JSON extension cannot be downloaded)');
else {
  await check('JSON array source → JSON array target (downloads the JSON extension)', async () => {
    await page.click('#btnNew'); await page.waitForTimeout(300);
    const id = await jsonMapping('orders.json', JSON.stringify(ROWS), 'auto', 'array');
    await page.waitForTimeout(800); await run(page);
    const s = await summary(page); expect(s?.ok && s.rows === 2, 'run ' + JSON.stringify(s) + ' · ' + await page.evaluate(() => DataForge.state.log.slice(-3).map((l) => l.text || l.message || '').join(' | ')));
    const out = JSON.parse(await targetText(id));
    expect(Array.isArray(out) && out.length === 2 && out[0].name === ROWS[0].name && out[1].name === 'ünïcode' && out[0].tags.join() === 'a,b', 'unexpected JSON output ' + JSON.stringify(out).slice(0, 200));
    return '2 of 3 rows written as a JSON array';
  });
  await check('newline-delimited JSON source → newline-delimited JSON target', async () => {
    await page.click('#btnNew'); await page.waitForTimeout(300);
    const id = await jsonMapping('orders.ndjson', ROWS.map((r) => JSON.stringify(r)).join('\n') + '\n', 'newline_delimited', 'newline_delimited');
    await page.waitForTimeout(800); await run(page);
    const s = await summary(page); expect(s?.ok && s.rows === 2, 'run ' + JSON.stringify(s));
    const lines = (await targetText(id)).trim().split('\n').map((l) => JSON.parse(l));
    expect(lines.length === 2 && lines[0].id === 1 && lines[1].id === 3, 'unexpected NDJSON output ' + JSON.stringify(lines).slice(0, 200));
    const code = await page.evaluate(() => ['snowflake', 'pyspark', 'dbt', 'duckdb'].map((d) => { try { const g = DataForge.CodeGen.generate(d); return (g.files ? Object.values(g.files).join('') : g.text).length; } catch (e) { return 'error: ' + e.message; } }));
    expect(code.every((n) => typeof n === 'number' && n > 200), 'JSON mapping export: ' + JSON.stringify(code));
    return '2 of 3 rows written as NDJSON; exports to every dialect';
  });
}
await check('share link opens in another tab; the first tab is warned about the change', async () => {
  await page.click('#repoBody button:text-is("Share link")'); await page.waitForTimeout(400);
  const link = await page.evaluate(() => navigator.clipboard.readText());
  expect(link.startsWith(base + '#m='), 'clipboard holds ' + link.slice(0, 60));
  const other = await open(link);
  await page.waitForTimeout(600);
  expect(!(await page.evaluate(() => DataForge.Repo.stale)), 'opening a second tab alone triggered the multi-tab warning');
  await dialog(other, 'Import mapping'); await other.waitForTimeout(700);
  expect(await other.evaluate(() => DataForge.state.doc.nodes.length === 3 && location.hash === ''), 'shared mapping not imported');
  await page.waitForTimeout(300);
  expect(await page.evaluate(() => DataForge.Repo.stale), 'first tab was not told about the change');
  expect((await page.$$eval('#toasts .toast', (t) => t.map((e) => e.textContent))).some((t) => /another DataForge tab/.test(t)), 'no multi-tab warning shown');
  await other.close();
  const bad = await open(base + '#m=AAAA'); await bad.waitForTimeout(500);
  expect((await bad.$$eval('#toasts .toast', (t) => t.map((e) => e.textContent))).some((t) => /Invalid shared mapping link/.test(t)), 'bad link not reported');
  await bad.close();
});
await check('phone layout: drawers, tap to add, run', async () => {
  await page.setViewportSize({ width: 390, height: 800 }); await page.waitForTimeout(200);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= 390), 'page overflows horizontally');
  const n = await page.evaluate(() => DataForge.state.doc.nodes.length);
  await page.click('#btnPanelLeft'); await page.click('.pal-item:has-text("Sorter")'); await page.waitForTimeout(200);
  expect(await page.evaluate(() => DataForge.state.doc.nodes.length) === n + 1, 'tap did not add a node');
  await page.evaluate(() => DataForge.Actions.remove());
  await page.click('#btnPanelRight'); expect(await page.isVisible('#props'), 'properties drawer hidden'); await page.keyboard.press('Escape');
  await run(page); expect((await summary(page))?.ok, 'run failed on the phone layout');
  await page.setViewportSize({ width: 1440, height: 900 });
});
await check('unexpected errors are reported to the user', async () => {
  const before = problems.length;
  await page.evaluate(() => { setTimeout(() => { throw new Error('smoke-test failure'); }, 0); }); await page.waitForTimeout(300);
  problems.splice(before);   // the deliberate error above is not a problem
  expect((await page.$$eval('#toasts .toast.error', (t) => t.map((e) => e.textContent))).some((t) => /smoke-test failure/.test(t)), 'no error toast');
});

await browser.close(); server.close();
const failed = results.filter((r) => r[0] === 'FAIL').length;
if (problems.length) console.log('\nProblems seen in the browser:\n  ' + [...new Set(problems)].join('\n  '));
console.log(`\n${results.length - failed}/${results.length} checks passed` + (problems.length ? `, ${new Set(problems).size} browser problem(s)` : ''));
process.exit(failed || problems.length ? 1 : 0);
