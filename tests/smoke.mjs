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
function mirrorRoute(route) {
  const rest = /duckdb-wasm@[^/]+\/(.*)$/.exec(route.request().url())?.[1];
  const file = rest === '+esm' ? join(mirror, 'esm.js') : rest ? join(mirror, rest) : null;
  if (!file || !existsSync(file)) return route.abort();
  return route.fulfill({ body: readFileSync(file), contentType: TYPES[extname(file)] || 'text/javascript', headers: { 'access-control-allow-origin': '*' } });
}
if (mirror) await context.route('https://cdn.jsdelivr.net/**', mirrorRoute);

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
await check('opens in the dark theme', async () => {
  const theme = await page.evaluate(() => document.documentElement.dataset.theme);
  expect(theme === 'dark', 'theme is ' + theme);
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
  await page.click('.monitor-toolbar input[type=search]'); await page.keyboard.type('zzz');
  expect(await page.inputValue('.monitor-toolbar input[type=search]') === 'zzz', 'monitor search loses focus while typing');
  await page.fill('.monitor-toolbar input[type=search]', '');
  await page.locator('.monitor-table tbody tr[data-fk^="run-"]').last().focus(); await page.keyboard.press('Enter');
  expect(await page.evaluate(() => document.activeElement?.matches('tr.chosen')), 'a run cannot be chosen from the keyboard');
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
  // Run straight after opening the mapping: the run waits for its stored file instead of failing.
  const uploadId = await page.evaluate(() => DataForge.state.doc.mapping.id), otherId = await page.evaluate((u) => DataForge.Repo.project.mappings.find((m) => m.mapping.id !== u).mapping.id, uploadId);
  await page.evaluate((o) => DataForge.Repo.openMapping(o), otherId); await page.waitForTimeout(300);
  await page.evaluate((u) => { DataForge.Repo.openMapping(u); DataForge.Runner.run(); }, uploadId); await page.waitForTimeout(300); await idle(page);
  s = await summary(page); expect(s?.ok && s.rows === 2, 'run started while the mapping was opening ' + JSON.stringify(s));
});
let projectFile, projectSnapshot;
await check('project section: rename, upload without "remember", switch mappings, save .dfproj', async () => {
  expect(await page.isVisible('#projectNav'), 'project section missing');
  await page.fill('#projectName', 'Retail Warehouse'); await page.press('#projectName', 'Enter'); await page.waitForTimeout(400);
  expect(await page.evaluate(() => DataForge.Repo.project.project.name) === 'Retail Warehouse', 'rename not stored');
  // A mapping whose upload is NOT remembered on this device: the project file must still carry its data.
  await page.click('#btnNew'); await page.waitForTimeout(300);
  await page.evaluate(async () => {
    const { Actions: A, state, History } = DataForge;
    state.doc.mapping.name = 'Unremembered upload'; History.commit();
    A.addNode('csv_source', 60, 100); A.addNode('filter', 340, 100); A.addNode('csv_target', 620, 100);
    const [src, fil, tgt] = state.doc.nodes; A.link(src.id, fil.id); A.link(fil.id, tgt.id);
    fil.config.condition = 'amount >= 20'; state.doc.parameters.$$MIN = { kind: 'parameter', type: 'INTEGER', value: '20' }; History.commit();
    await A.attachFile(src.id, new File(['id,amount\n1,10\n2,20\n3,30\n4,40\n'], 'payments.csv', { type: 'text/csv' }));
  });
  await page.waitForTimeout(800); await run(page);
  let s = await summary(page); expect(s?.ok && s.rows === 3, 'first run ' + JSON.stringify(s));
  // Switching away and back used to lose an upload that was not remembered.
  const ids = await page.evaluate(() => DataForge.Repo.project.mappings.map((m) => m.mapping.id));
  await page.evaluate((id) => DataForge.Repo.openMapping(id), ids[0]); await page.waitForTimeout(300);
  await page.evaluate((id) => DataForge.Repo.openMapping(id), ids[ids.length - 1]); await page.waitForTimeout(500);
  await run(page); s = await summary(page); expect(s?.ok && s.rows === 3, 'upload lost after switching mappings ' + JSON.stringify(s));
  await page.evaluate(async () => { await DataForge.Versions.save('before project file'); const R = DataForge.Repo; R.project.parameterSets.push({ id: 'ps_test', name: 'Prod values', values: { $$MIN: '30' } }); R.save(); R.render(); });
  await page.waitForTimeout(400);
  expect(await page.getAttribute('#projectState', 'data-state') !== 'clean', 'project shown as saved before any save');
  const dir = mkdtempSync(join(tmpdir(), 'dataforge-proj-'));
  const [dl] = await Promise.all([page.waitForEvent('download'), page.click('#btnProjectSave')]);
  expect(dl.suggestedFilename() === 'retail_warehouse.dfproj', 'file name ' + dl.suggestedFilename());
  projectFile = join(dir, dl.suggestedFilename()); await dl.saveAs(projectFile);
  const head = readFileSync(projectFile).subarray(0, 2);
  expect(head[0] === 0x1f && head[1] === 0x8b, 'project file is not gzip-compressed');
  await page.waitForTimeout(300);
  expect(await page.getAttribute('#projectState', 'data-state') === 'clean', 'state after save: ' + await page.textContent('#projectState'));
  projectSnapshot = await page.evaluate(() => { const R = DataForge.Repo; return { name: R.project.project.name, mappings: R.project.mappings.map((m) => m.mapping.name).sort(), workflows: R.project.workflows.length, sets: R.project.parameterSets.length, versions: Object.values(R.project.versions).flat().length, runs: R.runs.length, active: R.activeId }; });
  await page.evaluate(() => { DataForge.state.doc.mapping.name = 'Unremembered upload v2'; DataForge.History.commit(); });
  await page.waitForTimeout(500);
  expect(await page.getAttribute('#projectState', 'data-state') === 'dirty', 'edit after save not flagged');
  await page.evaluate(() => { DataForge.state.doc.mapping.name = 'Unremembered upload'; DataForge.History.commit(); });
  return `${(readFileSync(projectFile).length / 1024).toFixed(1)} KB, ${projectSnapshot.mappings.length} mappings, ${projectSnapshot.runs} runs`;
});
await check('fresh session: open the .dfproj, everything is back and runs without re-uploading', async () => {
  const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
  if (mirror) await fresh.route('https://cdn.jsdelivr.net/**', mirrorRoute);
  const p2 = await fresh.newPage(); watch(p2); p2.setDefaultTimeout(10000);
  await p2.goto(base);
  await p2.waitForFunction(() => window.DataForge?.Repo.project && document.querySelector('#enginePill').dataset.state !== 'loading', null, { timeout: 120000 });
  await p2.waitForTimeout(400);
  expect(await p2.evaluate(() => DataForge.Repo.project.mappings.length) === 0, 'fresh context is not empty');
  await p2.click('#generalModal >> text=Continue');
  await p2.setInputFiles('#projectInput', projectFile); await p2.waitForTimeout(1200);
  const got = await p2.evaluate(() => { const R = DataForge.Repo; return { name: R.project.project.name, mappings: R.project.mappings.map((m) => m.mapping.name).sort(), workflows: R.project.workflows.length, sets: R.project.parameterSets.length, versions: Object.values(R.project.versions).flat().length, runs: R.runs.length, active: R.activeId }; });
  expect(got.versions >= 1 && got.sets >= 1, 'version or parameter set missing from the snapshot ' + JSON.stringify(got));
  expect(JSON.stringify(got) === JSON.stringify(projectSnapshot), 'restored ' + JSON.stringify(got) + '\n       expected ' + JSON.stringify(projectSnapshot));
  expect(!(await p2.isVisible('#welcomeStage')), 'welcome screen still shown');
  // The mapping that was open when saving is open again, with its data and parameters.
  expect(await p2.evaluate(() => DataForge.state.doc.mapping.name === 'Unremembered upload' && DataForge.state.doc.parameters.$$MIN?.value === '20'), 'wrong mapping or parameters reopened');
  await run(p2); let s = await summary(p2); expect(s?.ok && s.rows === 3, 'unremembered upload did not travel: ' + JSON.stringify(s));
  const csv = await p2.evaluate(() => DataForge.Repo.project.mappings.find((m) => m.nodes.some((n) => n.config.fileName === 'my data (1).csv'))?.mapping.id);
  await p2.evaluate((id) => DataForge.Repo.openMapping(id), csv); await p2.waitForTimeout(500);
  await run(p2); s = await summary(p2); expect(s?.ok && s.rows === 2, 'remembered upload did not travel: ' + JSON.stringify(s));
  await p2.waitForTimeout(1600);
  expect(await p2.getAttribute('#projectState', 'data-state') === 'clean', 'opened project not shown as up to date: ' + await p2.textContent('#projectState'));
  // Reload in the new session: the restored data is now remembered on this device too.
  await p2.reload();
  await p2.waitForFunction(() => window.DataForge?.Repo.project && document.querySelector('#enginePill').dataset.state !== 'loading', null, { timeout: 120000 });
  await p2.waitForTimeout(600);
  const pay = await p2.evaluate(() => DataForge.Repo.project.mappings.find((m) => m.mapping.name === 'Unremembered upload').mapping.id);
  await p2.evaluate((id) => DataForge.Repo.openMapping(id), pay); await p2.waitForTimeout(500);
  await run(p2); s = await summary(p2); expect(s?.ok && s.rows === 3, 'data lost after reload in the new session: ' + JSON.stringify(s));
  // The sample workflow runs too (sample data is re-attached automatically).
  await p2.evaluate(() => DataForge.Workflow.open(DataForge.Repo.project.workflows[0].id)); await p2.waitForTimeout(300);
  await run(p2);
  const wf = await p2.evaluate(() => DataForge.Repo.runs.find((r) => r.type === 'workflow')?.status);
  expect(wf === 'SUCCEEDED', 'workflow run ' + wf);
  // New project: offers to save first, then starts empty.
  await p2.click('#btnProjectMenu'); await p2.click('#ctxMenu button:has-text("New project")');
  await dialog(p2, 'Discard and start new'); await p2.waitForTimeout(500);
  const after = await p2.evaluate(() => ({ n: DataForge.Repo.project.mappings.length + DataForge.Repo.project.workflows.length, runs: DataForge.Repo.runs.length, name: DataForge.Repo.project.project.name }));
  expect(after.n === 0 && after.runs === 0 && after.name === 'My DataForge Project', 'new project ' + JSON.stringify(after));
  await fresh.close();
  return `${got.mappings.length} mappings, ${got.workflows} workflows, ${got.versions} versions, ${got.runs} runs restored`;
});
const fillDialog = async (pg, label, text) => { await pg.waitForSelector('#dialogOverlay:not([hidden])'); await pg.fill('#dialogBox input', text); await pg.click(`#dialogBox button:text-is("${label}")`); await pg.waitForTimeout(300); };
const newMapping = async (pg, nodes, wire) => pg.evaluate(async ([nodes, wire]) => {
  const { Actions: A, state, History, Repo } = DataForge;
  Repo.createMapping(); nodes.forEach(([type, x, y]) => A.addNode(type, x, y));
  const n = state.doc.nodes; wire.forEach(([a, b, port]) => A.link(n[a].id, n[b].id, null, port || null));
  History.commit(); return n.map((x) => x.id);
}, [nodes, wire]);
await check('profile: column statistics and personal-data hints', async () => {
  const [src] = await newMapping(page, [['csv_source', 60, 100], ['preview_target', 380, 100]], [[0, 1]]);
  await page.evaluate(async (id) => DataForge.Actions.attachFile(id, new File(['id,email,city\n1,a@x.com,Oslo\n2,b@y.org,\n3,c@z.net,Oslo\n'], 'people.csv')), src);
  await page.waitForTimeout(800);
  await page.evaluate((id) => DataForge.Profile.run(id), src); await page.waitForFunction(() => DataForge.Profile.result && !DataForge.Profile.result.loading);
  const r = await page.evaluate(() => DataForge.Profile.result);
  expect(!r.error && r.rows === 3 && r.columns.length === 3, 'profile ' + JSON.stringify(r).slice(0, 200));
  const by = Object.fromEntries(r.columns.map((c) => [c.name, c]));
  expect(by.id.distinct === 3 && by.email.pii === 'Email' && Math.round(by.city.nullPct) === 33 && by.city.top[0].value === 'Oslo', 'column stats ' + JSON.stringify(by).slice(0, 300));
  expect(await page.isVisible('#pane-profile .profile-table'), 'profile table not shown');
  expect((await page.textContent('#pane-profile')).includes('Personal data: Email'), 'personal-data hint missing');
  return '3 rows · key, email and fill rate detected';
});
await check('SQL node: two inputs, detected types, one statement only', async () => {
  const [a, b, q] = await newMapping(page, [['csv_source', 60, 60], ['csv_source', 60, 260], ['sql_transform', 360, 140], ['preview_target', 640, 140]], [[0, 2, 'in'], [1, 2, 'in2'], [2, 3]]);
  await page.evaluate(async ([a, b, q]) => {
    const { Actions: A, Graph, History } = DataForge;
    await A.attachFile(a, new File(['cust,amount\n1,10\n1,5\n2,7\n'], 'orders.csv')); await A.attachFile(b, new File(['cust,name\n1,Ana\n2,Bo\n'], 'customers.csv'));
    Graph.node(q).config.sql = 'SELECT c.name, SUM(o.amount) AS total\nFROM input o JOIN input2 c ON o.cust = c.cust\nGROUP BY c.name\nORDER BY c.name;'; History.commit();
  }, [a, b, q]);
  await page.waitForTimeout(1200);
  const ports = await page.evaluate((q) => DataForge.Graph.node(q).ports.out.map((p) => p.name + ':' + p.type).join(','), q);
  expect(ports === 'name:VARCHAR,total:HUGEINT' || ports === 'name:VARCHAR,total:BIGINT' || /^name:VARCHAR,total:/.test(ports), 'ports ' + ports);
  await run(page); const s = await summary(page); expect(s?.ok, 'SQL mapping run ' + JSON.stringify(s));
  const rows = await page.evaluate(() => DataForge.state.preview?.rows?.map((r) => Object.values(r).join('=')) || DataForge.state.preview);
  expect(JSON.stringify(rows).includes('Ana') && JSON.stringify(rows).includes('15'), 'preview ' + JSON.stringify(rows).slice(0, 200));
  const issues = await page.evaluate(async (q) => { const D = DataForge; D.Graph.node(q).config.sql = 'SELECT * FROM input; DROP TABLE x'; D.History.commit(); await D.Actions.validate(); return D.state.issues.map((i) => i.message); }, q);
  expect(issues.some((m) => /Only one statement/.test(m)), 'second statement not rejected: ' + JSON.stringify(issues));
  return 'join over input and input2: ' + ports;
});
await check('Excel: write a workbook, read it back with types and a chosen sheet', async () => {
  const [src, , tgt] = await newMapping(page, [['csv_source', 60, 100], ['filter', 330, 100], ['excel_target', 600, 100]], [[0, 1], [1, 2]]);
  await page.evaluate(async ([src, tgt]) => {
    const { Actions: A, Graph, History, state } = DataForge;
    await A.attachFile(src, new File(['region,amount,day,ok\nEast,10.5,2026-01-01,true\nWest,4,2026-01-02,false\nNorth,-1,2026-01-03,true\n'], 'sales.csv'));
    state.doc.nodes.find((n) => n.type === 'filter').config.condition = 'amount > 0'; Graph.node(tgt).config.sheetName = 'Totals'; History.commit();
  }, [src, tgt]);
  await page.waitForTimeout(900); await run(page);
  expect((await summary(page))?.ok, 'Excel target run failed');
  const back = await page.evaluate(async (tgt) => {
    const D = DataForge, bytes = new Uint8Array(await (await fetch(D.state.outputs[tgt].url)).arrayBuffer());
    D.Repo.createMapping(); D.Actions.addNode('excel_source', 60, 100); D.Actions.addNode('preview_target', 380, 100);
    const [x, p] = D.state.doc.nodes; D.Actions.link(x.id, p.id); x.config.sheet = 'Totals'; D.History.commit();
    await D.Actions.attachFile(x.id, new File([bytes], 'totals.xlsx')); await new Promise((r) => setTimeout(r, 1200));
    return { sheets: await D.XLSX.sheetNames(bytes), ports: x.ports.out.map((p) => p.name + ':' + p.type).join(',') };
  }, tgt);
  expect(back.sheets.join() === 'Totals' && back.ports === 'region:VARCHAR,amount:DOUBLE,day:DATE,ok:BOOLEAN', 'read back ' + JSON.stringify(back));
  await run(page); const s = await summary(page); expect(s?.ok, 'Excel source run ' + JSON.stringify(s));
  const rows = await page.evaluate(() => DataForge.state.preview?.rows?.length);
  expect(rows === 2, 'expected 2 rows back, got ' + rows);
  return back.ports;
});
if (mirror) console.log('  skip Parquet target and source (offline run: the Parquet extension cannot be downloaded)');
else await check('Parquet: write a target, read it back as a source', async () => {
  const [src, , tgt] = await newMapping(page, [['csv_source', 60, 100], ['filter', 330, 100], ['parquet_target', 600, 100]], [[0, 1], [1, 2]]);
  await page.evaluate(async (src) => { const D = DataForge; await D.Actions.attachFile(src, new File(['a,b\n1,x\n2,y\n3,z\n'], 'abc.csv')); D.state.doc.nodes.find((n) => n.type === 'filter').config.condition = 'a > 1'; D.History.commit(); }, src);
  await page.waitForTimeout(900); await run(page); expect((await summary(page))?.ok, 'Parquet target run failed');
  await page.evaluate(async (tgt) => {
    const D = DataForge, bytes = new Uint8Array(await (await fetch(D.state.outputs[tgt].url)).arrayBuffer());
    D.Repo.createMapping(); D.Actions.addNode('parquet_source', 60, 100); D.Actions.addNode('preview_target', 380, 100);
    const [x, p] = D.state.doc.nodes; D.Actions.link(x.id, p.id); D.History.commit();
    await D.Actions.attachFile(x.id, new File([bytes], 'abc.parquet')); await new Promise((r) => setTimeout(r, 1200));
  }, tgt);
  await run(page); expect((await summary(page))?.rows === 0 || (await summary(page))?.ok, 'Parquet source run failed');
  expect(await page.evaluate(() => DataForge.state.preview?.rows?.length) === 2, 'Parquet round trip lost rows');
});
await check('mapping tests: capture, pass, catch a logic change, travel with the project file', async () => {
  await page.click('#btnSampleMenu'); await page.click('[data-sample="original"]'); await page.waitForTimeout(700);
  await page.click('.tab[data-tab="tests"]'); await page.click('#pane-tests button:text-is("New test from current data")');
  await page.waitForFunction(() => DataForge.state.doc.tests?.length === 1 && !DataForge.MappingTests.busy);
  expect(await page.evaluate(() => DataForge.MappingTests.results[DataForge.state.doc.tests[0].id]?.status) === 'pass', 'captured test does not pass');
  await page.evaluate(() => { const f = DataForge.state.doc.nodes.find((n) => n.type === 'filter'); f.config.condition = "status = 'CANCELLED'"; DataForge.History.commit(); });
  await page.click('#pane-tests button:text-is("Run all tests")'); await page.waitForFunction(() => !DataForge.MappingTests.busy);
  const detail = await page.textContent('#pane-tests .test-detail');
  expect(/expected rows? missing/.test(detail), 'logic change not caught: ' + detail);
  expect(await page.textContent('#testCnt') === '1✕', 'tab badge ' + await page.textContent('#testCnt'));
  await page.evaluate(() => { const f = DataForge.state.doc.nodes.find((n) => n.type === 'filter'); f.config.condition = "status = 'DELIVERED'"; DataForge.History.commit(); });
  const ok = await page.evaluate(() => DataForge.MappingTests.runAll());
  expect(ok?.failed === 0, 'fixed logic still fails');
  const json = await page.evaluate(() => JSON.stringify(DataForge.Repo.normalize(JSON.parse(JSON.stringify(DataForge.Repo.project))).mappings.find((m) => m.mapping.id === DataForge.Repo.activeId).tests));
  expect(JSON.parse(json).length === 1, 'tests lost when the project is saved and opened');
  return 'pass → fail on change → pass';
});
await check('components: save a selection, insert it into another mapping', async () => {
  await page.evaluate(() => { const D = DataForge; D.Actions.select(D.state.doc.nodes.filter((n) => ['filter', 'expression'].includes(n.type)).map((n) => n.id)); });
  const saving = page.evaluate(() => DataForge.Components.save()); await fillDialog(page, 'Save component', 'Reusable price block'); await saving;
  expect((await page.textContent('#repoTree')).includes('Components (1)'), 'component not listed in the repository');
  await page.evaluate(() => DataForge.Repo.createMapping()); await page.waitForTimeout(200);
  await page.dblclick('#repoTree .repo-item:has-text("Reusable price block") .repo-open'); await page.waitForTimeout(400);
  const got = await page.evaluate(() => ({ nodes: DataForge.state.doc.nodes.map((n) => n.type).join(), links: DataForge.state.doc.links.length }));
  expect(got.nodes === 'filter,expression' && got.links === 1, 'inserted ' + JSON.stringify(got));
});
await check('password-protected project file: unreadable without it, wrong password rejected', async () => {
  await page.click('#btnProjectMenu'); await page.click('#ctxMenu button:has-text("Protect with a password")');
  await fillDialog(page, 'Continue', 'correct horse');
  const [dl] = await Promise.all([page.waitForEvent('download'), fillDialog(page, 'Continue', 'correct horse')]);
  const file = join(mkdtempSync(join(tmpdir(), 'dataforge-enc-')), dl.suggestedFilename()); await dl.saveAs(file);
  const text = readFileSync(file, 'utf8'), env = JSON.parse(text);
  expect(env.format === 'dataforge-project-encrypted' && env.cipher.name === 'AES-GCM' && !/Reusable price block|SRC_retail/.test(text), 'file is not encrypted');
  expect((await page.textContent('#projectState')).includes('Password-protected'), 'status does not mention the password');
  const fresh = await browser.newContext({ viewport: { width: 1440, height: 900 } }); if (mirror) await fresh.route('https://cdn.jsdelivr.net/**', mirrorRoute);
  const p3 = await fresh.newPage(); watch(p3); p3.setDefaultTimeout(10000); await p3.goto(base);
  await p3.waitForFunction(() => window.DataForge?.Repo.project && document.querySelector('#enginePill').dataset.state !== 'loading', null, { timeout: 120000 });
  await p3.click('#generalModal >> text=Continue');
  await p3.setInputFiles('#projectInput', file); await fillDialog(p3, 'Open', 'not it');
  expect(/not correct/.test(await p3.textContent('#dialogBox p')), 'wrong password accepted');
  await fillDialog(p3, 'Open', 'correct horse'); await p3.waitForTimeout(1200);
  const got = await p3.evaluate(() => ({ comps: DataForge.Repo.project.components.length, maps: DataForge.Repo.project.mappings.length, pw: !!DataForge.ProjectFile.password }));
  expect(got.comps === 1 && got.maps > 3 && got.pw, 'opened ' + JSON.stringify(got));
  await fresh.close();
  await page.click('#btnProjectMenu'); await page.click('#ctxMenu button:has-text("Remove password")');
  // Later checks share the mapping that was open before this block.
  await page.evaluate(() => DataForge.Repo.openMapping(DataForge.Repo.project.mappings.find((m) => m.mapping.name === 'Unremembered upload').mapping.id)); await page.waitForTimeout(500);
  return 'AES-256-GCM, PBKDF2 ' + env.kdf.iterations;
});
await check('project lineage: impact of a column across chained mappings, governance and masking', async () => {
  const r = await page.evaluate(async () => {
    const D = DataForge, A = D.Actions, S = D.state, sleep = (ms) => new Promise((x) => setTimeout(x, ms));
    D.Repo.createMapping(); S.doc.mapping.name = 'Lineage A';
    A.addNode('csv_source', 60, 100); A.addNode('expression', 330, 100); A.addNode('csv_target', 600, 100);
    let [s, e, t] = S.doc.nodes; A.link(s.id, e.id); A.link(e.id, t.id);
    e.config.fields = [{ name: 'email_domain', expression: "split_part(email,'@',2)" }]; t.config.fileName = 'lin_clean.csv'; D.History.commit();
    await A.attachFile(s.id, new File(['id,email,spend\n1,ana@x.com,10\n2,bo@y.org,20\n'], 'lin_people.csv')); await sleep(1000);
    const first = S.doc.nodes[0].id;
    D.Repo.createMapping(); S.doc.mapping.name = 'Lineage B';
    A.addNode('csv_source', 60, 100); A.addNode('aggregator', 330, 100); A.addNode('excel_target', 600, 100);
    [s, e, t] = S.doc.nodes; A.link(s.id, e.id); A.link(e.id, t.id); s.config.fileName = 'lin_clean.csv';
    s.ports.out = [{ name: 'id', type: 'BIGINT' }, { name: 'email', type: 'VARCHAR' }, { name: 'spend', type: 'BIGINT' }, { name: 'email_domain', type: 'VARCHAR' }];
    e.config.groupBy = ['email_domain']; e.config.fields = [{ name: 'total', expression: 'SUM(spend)' }]; t.config.fileName = 'lin_report.xlsx'; D.History.commit(); await sleep(600);
    const m = D.ProjectLineage.build(), imp = D.ProjectLineage.impact(m, 'lin_people.csv', 'email', 'down');
    const inv = D.Governance.inventory().filter((x) => x.dataset.startsWith('lin_'));
    await D.Repo.openMapping(D.Repo.project.mappings.find((x) => x.mapping.name === 'Lineage A').mapping.id); await sleep(500);
    D.Governance.settings().maskPreview = true; await D.Preview.node(S.doc.nodes.find((n) => n.type === 'csv_source').id); await sleep(300);
    const cells = [...document.querySelectorAll('#pane-preview tbody tr:first-child td')].map((td) => td.textContent);
    D.Governance.settings().maskPreview = false;
    return { roles: [...m.datasets.values()].filter((d) => d.key.startsWith('lin_')).map((d) => d.key + ':' + d.role).sort(), impact: imp.steps.map((x) => x.dataset + '.' + x.column), mappings: imp.mappings,
      email: inv.find((x) => x.dataset === 'lin_people.csv' && x.column === 'email')?.label, derived: inv.find((x) => x.dataset === 'lin_report.xlsx' && x.column === 'email_domain')?.inherited, cells };
  });
  expect(r.roles.join() === 'lin_clean.csv:intermediate,lin_people.csv:input,lin_report.xlsx:output', 'roles ' + r.roles);
  expect(r.impact.includes('lin_report.xlsx.email_domain') && r.mappings.length === 2, 'impact ' + JSON.stringify(r));
  expect(r.email === 'Email' && /lin_people\.csv\.email/.test(r.derived || ''), 'classification ' + JSON.stringify(r));
  expect(r.cells.includes('a•••@x.com') && !r.cells.includes('ana@x.com'), 'email not masked: ' + r.cells.join('|'));
  await page.evaluate(() => DataForge.ProjectLineage.open()); await page.waitForTimeout(300);
  expect(await page.isVisible('#generalModal .plin-svg'), 'project lineage diagram not shown');
  await page.keyboard.press('Escape');
  return 'people.email → clean.email_domain → report.email_domain';
});
await check('data contract: fail blocks the write, warn writes and reports', async () => {
  const r = await page.evaluate(async () => {
    const D = DataForge, S = D.state, tgt = S.doc.nodes.find((n) => n.type === 'csv_target');
    tgt.config.contract = { mode: 'fail', allowExtra: true, columns: [{ name: 'id', type: 'BIGINT', unique: true, required: true }, { name: 'email', type: 'VARCHAR', required: true }, { name: 'not_there', type: '' }] }; D.History.commit();
    await D.Runner.run(); const failed = S.log.filter((l) => l.kind === 'step').pop(), wrote1 = !!S.outputs[tgt.id];
    tgt.config.contract.mode = 'warn'; D.History.commit(); await D.Runner.run(); const warned = S.log.filter((l) => l.kind === 'step').pop(), wrote2 = !!S.outputs[tgt.id];
    tgt.config.contract.columns.pop(); D.History.commit(); await D.Runner.run(); const clean = S.log.filter((l) => l.kind === 'step').pop();
    return { failed: failed.status + ' ' + failed.note, wrote1, warned: warned.status + ' ' + warned.note, wrote2, clean: clean.status + ' ' + (clean.note || '') };
  });
  expect(/^fail Data contract failed: missing column not_there/.test(r.failed) && !r.wrote1, 'fail mode ' + JSON.stringify(r));
  expect(/^ok .*missing column not_there/.test(r.warned) && r.wrote2, 'warn mode ' + JSON.stringify(r));
  expect(r.clean === 'ok ', 'contract that holds still reported: ' + r.clean);
});
await check('Airflow export: DAG, trigger rules, one job per mapping, chained-file warning', async () => {
  const r = await page.evaluate(() => {
    const D = DataForge, w = D.Repo.createWorkflow(), id = (n) => D.Repo.project.mappings.find((m) => m.mapping.name === n).mapping.id;
    w.name = 'Lineage nightly'; w.scheduleMinutes = 30;
    const add = (type, name, cfg) => { const n = { id: 't' + (w.nodes.length + 1), type, name, position: { x: 100 * w.nodes.length, y: 150 }, config: { ...D.TaskRegistry.get(type).defaultConfig(), ...cfg } }; w.nodes.push(n); return n; };
    const a = add('session', 'Run A', { mappingId: id('Lineage A') }), b = add('session', 'Run B', { mappingId: id('Lineage B') }), alert = add('notification', 'Tell ops', { message: 'A failed' });
    w.links.push({ id: 'wl1', from: 't1', to: a.id, condition: 'always', expression: '' }, { id: 'wl2', from: a.id, to: b.id, condition: 'success', expression: '' }, { id: 'wl3', from: a.id, to: alert.id, condition: 'failure', expression: '' });
    const out = D.AirflowExport.build(w); return { files: Object.keys(out.files), dag: out.files['dags/lineage_nightly.py'], warnings: out.warnings };
  });
  expect(r.files.includes('jobs/lineage_a.py') && r.files.includes('jobs/lineage_b.py') && r.files.includes('README.md'), 'files ' + r.files);
  expect(/schedule=timedelta\(minutes=30\)/.test(r.dag) && /tell_ops = BashOperator\([^\n]*trigger_rule="one_failed"/.test(r.dag) && /run_a >> run_b/.test(r.dag), 'DAG ' + r.dag);
  expect(r.warnings.some((x) => /Lineage B reads lin_clean\.csv, which Lineage A writes/.test(x)), 'no chained-file warning ' + JSON.stringify(r.warnings));
  await page.evaluate(() => DataForge.Repo.openMapping(DataForge.Repo.project.mappings.find((m) => m.mapping.name === 'Unremembered upload').mapping.id)); await page.waitForTimeout(500);
  return r.files.length + ' files';
});
await check('generated code: names, parameter values and messages stay data (no code injection)', async () => {
  const r = await page.evaluate(() => {
    const D = DataForge, doc = D.state.doc, saved = JSON.stringify({ names: doc.nodes.map((n) => n.name), parameters: doc.parameters, name: doc.mapping.name });
    const PAY = 'x\nimport os; os.system("echo PWNED")\r\nDROP TABLE t; --\u2028y';
    doc.nodes.forEach((n) => { n.name += PAY; });
    doc.parameters = { ...doc.parameters, $$EVIL: { kind: 'parameter', type: 'VARCHAR', value: "x\\'; DROP TABLE t; --" } };
    const files = {};
    for (const d of ['snowflake', 'pyspark', 'dbt', 'duckdb']) { const g = D.CodeGen.generate(d); Object.assign(files, g.files ? Object.fromEntries(Object.entries(g.files).map(([k, v]) => [d + ':' + k, v])) : { [d]: g.text }); }
    const back = JSON.parse(saved); doc.nodes.forEach((n, i) => { n.name = back.names[i]; }); doc.parameters = back.parameters; doc.mapping.name = back.name;
    const w = D.Workflow.normalize({ name: 'Inject\nme', nodes: [{ id: 't1', type: 'start', name: 'Start', position: { x: 0, y: 0 }, config: {} }, { id: 't2', type: 'notification', name: 'Alert\nimport os', position: { x: 200, y: 0 }, config: { message: "done $(touch /tmp/pwned) `id` {{ var }} {% if 1 %}x{% endif %} it's" } }], links: [{ id: 'wl1', from: 't1', to: 't2', condition: 'always', expression: '' }] });
    const dag = Object.entries(D.AirflowExport.build(w).files).find(([k]) => k.startsWith('dags/'))[1];
    return { files, dag, taskName: w.nodes[1].name };
  });
  // A payload line may only appear inside a comment; the code that would run must never start with it.
  const leaked = Object.entries(r.files).flatMap(([f, text]) => text.split(/\r\n|\r|\n|\u2028/).filter((l) => /^\s*(import os|DROP TABLE)/.test(l)).map((l) => f + ': ' + l.trim()));
  expect(!leaked.length, 'payload escaped a comment: ' + leaked.slice(0, 3).join(' | '));
  const set = r.files.snowflake.split('\n').find((l) => l.startsWith('SET evil'));
  expect(set === "SET evil = 'x\\\\''; DROP TABLE t; --';", 'Snowflake SET does not escape backslashes: ' + set);
  const bash = /bash_command="echo '([^\n]*)'"/.exec(r.dag)?.[1] || '';
  expect(bash.includes('$(touch /tmp/pwned)') && !/\{\{|\{%/.test(bash) && /it'\\\\''s/.test(bash), 'Airflow notification is not a single quoted shell word: ' + bash);
  expect(!/\n/.test(r.taskName), 'imported task names keep line breaks');
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
await check('keyboard: build mappings and workflows without a mouse (focus, move, Connect to, edit, delete)', async () => {
  await page.selectOption('#studioMode', 'mapping');
  const previous = await page.evaluate(() => DataForge.state.doc.mapping.id);
  await page.click('#btnNew'); await page.waitForTimeout(300);
  await page.evaluate(() => { const A = DataForge.Actions; A.addNode('csv_source', 60, 100); A.addNode('filter', 340, 100); DataForge.History.commit(); });
  await page.focus('.node >> nth=0');
  expect(await page.evaluate(() => DataForge.state.sel.has('n1')), 'focusing a node does not select it');
  const x0 = await page.evaluate(() => DataForge.state.doc.nodes[0].position.x);
  await page.keyboard.press('ArrowRight');
  expect(await page.evaluate((x) => DataForge.state.doc.nodes[0].position.x === x + 10 && document.activeElement?.dataset.id === 'n1', x0), 'arrow key did not move the node or focus was lost');
  await page.keyboard.press('Shift+F10'); await page.waitForSelector('#ctxMenu:not([hidden])');
  const items = await page.$$eval('#ctxMenu button', (b) => b.map((x) => x.textContent));
  const i = items.findIndex((t) => t.startsWith('Connect to'));
  expect(i >= 0, 'no Connect to item: ' + items.join(' | '));
  for (let k = 0; k < i; k++) await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter'); await page.waitForTimeout(150);
  expect(await page.evaluate(() => DataForge.state.doc.links.some((l) => l.from === 'n1' && l.to === 'n2')), 'Connect to did not link the nodes');
  await page.focus('.node >> nth=1'); await page.keyboard.press('Enter');
  expect(await page.evaluate(() => !!document.activeElement?.closest('#props')), 'Enter did not move focus to the properties');
  await page.focus('.node >> nth=1'); await page.keyboard.press('Delete'); await page.waitForTimeout(100);
  expect(await page.evaluate(() => DataForge.state.doc.nodes.length === 1 && document.activeElement?.dataset.id === 'n1'), 'Delete did not remove the node and focus the next one');
  await page.evaluate((id) => DataForge.Repo.openMapping(id), previous); await page.waitForTimeout(300);
  await page.selectOption('#studioMode', 'workflow'); await page.waitForTimeout(300);
  await page.focus('.wf-node >> nth=0'); await page.keyboard.press('Shift+F10'); await page.waitForSelector('#ctxMenu:not([hidden])');
  expect((await page.$$eval('#ctxMenu button', (b) => b.map((x) => x.textContent))).some((t) => t.startsWith('Connect to')), 'workflow task menu has no Connect to');
  await page.keyboard.press('Escape');
  expect(await page.evaluate(() => document.activeElement?.classList.contains('wf-node')), 'Escape did not return focus to the workflow task');
  await page.selectOption('#studioMode', 'mapping'); await page.waitForTimeout(300);
});
const axeSource = readFileSync(join(root, 'node_modules/axe-core/axe.min.js'), 'utf8');
async function axeViolations(p, where) {
  if (!(await p.evaluate(() => !!window.axe))) await p.evaluate(axeSource);
  const found = await p.evaluate(async () => (await window.axe.run(document, { resultTypes: ['violations'], preload: false })).violations
    .map((v) => `${v.id} (${v.impact}, ${v.nodes.length}): ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`));
  return found.map((f) => where + ': ' + f);
}
await check('accessibility (axe-core): mapping, results tabs, export, workflow, monitor, light theme', async () => {
  const found = [];
  await page.selectOption('#studioMode', 'mapping'); await page.waitForTimeout(300);
  await page.evaluate(() => DataForge.Actions.select([DataForge.state.doc.nodes[0].id])); await page.waitForTimeout(200);
  found.push(...await axeViolations(page, 'mapping'));
  for (const t of ['sql', 'validation', 'log', 'dq', 'lineage', 'profile', 'tests']) { await page.click(`.tab[data-tab="${t}"]`); await page.waitForTimeout(150); }
  found.push(...await axeViolations(page, 'tests tab'));
  await page.click('.tab[data-tab="preview"]'); await page.keyboard.press('ArrowRight');
  expect(await page.evaluate(() => document.activeElement?.dataset.tab === 'sql' && DataForge.state.tab === 'sql'), 'arrow keys do not move between drawer tabs');
  await page.click('#btnExportCode'); await page.waitForTimeout(250); found.push(...await axeViolations(page, 'export dialog')); await page.click('#codegenClose');
  await page.selectOption('#studioMode', 'workflow'); await page.waitForTimeout(300); found.push(...await axeViolations(page, 'workflow'));
  await page.selectOption('#studioMode', 'monitor'); await page.waitForTimeout(300); found.push(...await axeViolations(page, 'monitor'));
  await page.selectOption('#studioMode', 'mapping'); await page.waitForTimeout(300);
  await page.click('#btnTheme'); await page.waitForTimeout(200); found.push(...await axeViolations(page, 'light theme')); await page.click('#btnTheme');
  // The properties panel of every node type, with a row added to list editors so their controls exist.
  const previous = await page.evaluate(() => DataForge.state.doc.mapping.id);
  const types = await page.evaluate(() => DataForge.Registry.list().map((d) => d.type));
  for (const t of types) {
    await page.click('#btnNew'); await page.waitForTimeout(120);
    await page.evaluate(async (t) => {
      const { Actions: A, state, History, Registry } = DataForge;
      if (Registry.get(t).category === 'source') A.addNode(t, 60, 100);
      else { A.addNode('csv_source', 60, 100); A.addNode(t, 340, 100); A.link('n1', 'n2'); await A.attachFile('n1', new File(['id,name,amount\n1,a,2.5\n'], 's.csv', { type: 'text/csv' })); }
      History.commit(); A.select([state.doc.nodes[state.doc.nodes.length - 1].id]);
    }, t);
    await page.waitForTimeout(400);
    await page.evaluate(() => { for (const b of document.querySelectorAll('#props button')) if (/^\s*Add (field|rule|sort key|match condition|output field|column|group)/i.test(b.textContent)) { b.click(); break; } });
    await page.waitForTimeout(150);
    found.push(...(await page.evaluate(async () => (await window.axe.run('#props', { resultTypes: ['violations'], preload: false })).violations.map((v) => `${v.id}: ${v.nodes[0].target.join(' ')}`))).map((f) => t + ' properties: ' + f));
  }
  await page.evaluate((id) => DataForge.Repo.openMapping(id), previous); await page.waitForTimeout(300);
  expect(!found.length, found.join('\n       '));
  return `no violations in 6 views and ${types.length} property panels`;
});
await check('phone layout: drawers, tap to add, run', async () => {
  await page.setViewportSize({ width: 390, height: 800 }); await page.waitForTimeout(200);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= 390), 'page overflows horizontally');
  const reach = await page.evaluate(() => ['#btnRun', '#btnPanelLeft', '#btnPanelRight', '#studioMode'].filter((s) => { const r = document.querySelector(s).getBoundingClientRect(); return r.width < 28 || r.left < 0 || r.right > 390; }));
  expect(!reach.length, 'not fully on screen without scrolling: ' + reach.join(', '));
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
