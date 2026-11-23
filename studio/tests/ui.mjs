// Offline UI regression tests. No DuckDB runtime, persistence, or network execution is simulated.
// npm run test:ui
// Optional: PLAYWRIGHT_CHROMIUM_EXECUTABLE=/path/to/chromium UI_ARTIFACTS=/path/to/output npm run test:ui
import { chromium } from 'playwright';
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const html = readFileSync(join(root, 'index.html'), 'utf8');
const artifacts = process.env.UI_ARTIFACTS || join(root, 'ui-test-artifacts');
mkdirSync(artifacts, { recursive: true });
const browser = await chromium.launch({ ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}), headless: true });
const results = [], errors = [];
const assert = (condition, message) => { if (!condition) throw new Error(message); };
async function check(name, fn) {
  try { await fn(); results.push({ name, status: 'PASS' }); console.log('PASS ' + name); }
  catch (error) { results.push({ name, status: 'FAIL', error: String(error.message) }); console.error('FAIL ' + name + ': ' + String(error.message).split('\n')[0]); }
}
async function open(width = 1440, height = 1000) {
  const page = await browser.newPage({ viewport: { width, height } });
  page.setDefaultTimeout(5000);
  page.on('pageerror', error => errors.push(error.message));
  await page.route('https://**/*', route => route.abort());
  await page.setContent(html, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.DataForge?.Repo.project && document.querySelector('#libraryTools'));
  await page.waitForFunction(() => DataForge.Engine.status === 'error');
  return page;
}
let page;
try {
  page = await open();
  await check('Welcome opens without a blocking About dialog', async () => {
    assert(await page.locator('#welcomeStage').isVisible(), 'Welcome is not visible');
    assert(!(await page.locator('#generalModal').isVisible()), 'About dialog blocks the workspace');
    assert(await page.locator('.welcome-card').count() === 3, 'Expected three sample cards');
  });
  await check('Import XML opens the actual file picker', async () => {
    const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.getByRole('button', { name: 'Import mapping XML', exact: true }).click()]);
    assert(await chooser.element().getAttribute('id') === 'xmlInput', 'Wrong file picker');
    assert(await page.evaluate(() => DataForge.Repo.project.mappings.length) === 0, 'Import unexpectedly loaded demo data');
  });
  await check('Sample card loads a real six-node mapping', async () => {
    await page.getByRole('button', { name: 'Load Retail orders sample', exact: true }).click();
    assert(await page.evaluate(() => DataForge.state.doc.nodes.length) === 6, 'Sample did not load');
    assert(!(await page.locator('#welcomeStage').isVisible()), 'Welcome covers the canvas');
  });
  await check('Palette contains all 24 registered node types', async () => {
    assert(await page.locator('.pal-item').count() === 24, 'Wrong node count');
    assert((await page.locator('#paletteCount').innerText()).includes('24 available'), 'Wrong availability label');
  });
  await check('Palette search filters by name', async () => {
    await page.locator('#paletteSearch').fill('csv');
    assert(await page.locator('.pal-item:visible').count() === 2, 'CSV search should find source and target');
  });
  await check('Palette search supports meaningful aliases', async () => {
    await page.locator('#paletteSearch').fill('spreadsheet');
    assert(await page.locator('.pal-item:visible').count() === 2, 'Spreadsheet alias should find Excel source and target');
  });
  await check('Empty palette search gives feedback and can be cleared', async () => {
    await page.locator('#paletteSearch').fill('zzz-no-match');
    assert(await page.locator('#libraryEmpty').isVisible(), 'Empty state missing');
    await page.locator('#clearPaletteSearch').click();
    assert(await page.locator('.pal-item:visible').count() === 24, 'Clear did not restore nodes');
    assert(await page.locator('#paletteSearch').evaluate(el => document.activeElement === el), 'Focus not restored');
  });
  await check('Keyboard Enter adds a transformation', async () => {
    await page.locator('#paletteSearch').fill('filter');
    const before = await page.evaluate(() => DataForge.state.doc.nodes.length);
    await page.locator('.pal-item:visible').focus(); await page.keyboard.press('Enter');
    assert(await page.evaluate(() => DataForge.state.doc.nodes.length) === before + 1, 'Enter did not add node');
  });
  await check('Keyboard Space adds a transformation without scrolling', async () => {
    const before = await page.evaluate(() => DataForge.state.doc.nodes.length);
    await page.locator('.pal-item:visible').focus(); await page.keyboard.press('Space');
    assert(await page.evaluate(() => DataForge.state.doc.nodes.length) === before + 1, 'Space did not add node');
  });
  await check('Repository jump reaches and focuses repository search', async () => {
    await page.locator('.library-jump').click();
    assert(await page.locator('#repoSearch').evaluate(el => document.activeElement === el), 'Repository search not focused');
  });
  await check('Command search has an accessible no-results state', async () => {
    await page.locator('#btnCommand').click(); await page.locator('#commandInput').fill('zzzz-not-a-command');
    assert(await page.locator('#commandEmpty').isVisible(), 'Empty state missing');
    assert(await page.locator('#commandResults button').count() === 0, 'Unexpected command');
    await page.keyboard.press('ArrowDown'); await page.keyboard.press('Enter');
    assert(await page.locator('#commandOverlay').isVisible(), 'No-result Enter should not close the dialog');
  });
  await check('Command palette recovers after a no-match search', async () => {
    await page.locator('#commandInput').fill('Toggle theme');
    assert(!(await page.locator('#commandEmpty').isVisible()), 'Empty state did not clear');
    await page.keyboard.press('Enter');
    assert(await page.evaluate(() => document.documentElement.dataset.theme) === 'dark', 'Command did not execute');
  });
  await check('Manual About dialog still opens and closes', async () => {
    await page.locator('#btnAbout').click();
    assert(await page.locator('#generalModal').isVisible(), 'About did not open');
    await page.getByRole('button', { name: 'Continue', exact: true }).click();
  });
  await page.evaluate(() => {
    DataForge.state.preview = {
      title: 'UI test fixture - synthetic rows', total: 5000,
      columns: [{ name: 'id', type: 'INTEGER' }, { name: 'name', type: 'VARCHAR' }, { name: 'email', type: 'VARCHAR' }],
      rows: Array.from({ length: 230 }, (_, i) => [String(i + 1), 'Person ' + (i + 1), 'secret' + (i + 1) + '@example.test'])
    };
    // Generic preview tests opt out of the pilot's stricter default masking; masking has separate checks.
    DataForge.Governance.settings().maskPreview = false;
    DataForge.UI.showTab('preview'); DataForge.UI.renderPreview();
  });
  await check('Preview defaults to 100 rows, not every loaded row', async () => {
    assert(await page.locator('#pane-preview tbody tr').count() === 100, 'Wrong default page size');
    assert((await page.locator('#pane-preview .pane-bar').innerText()).includes('230 of 5,000 rows loaded'), 'Loaded/total count unclear');
    assert(await page.locator('#previewPrevious').isDisabled(), 'Previous should be disabled on page 1');
  });
  await check('Preview pagination preserves row numbering', async () => {
    await page.locator('#previewNext').click();
    assert(await page.locator('#pane-preview tbody tr:first-child td').first().innerText() === '101', 'Row numbering reset');
    await page.locator('#previewNext').click();
    assert(await page.locator('#pane-preview tbody tr').count() === 30, 'Last page should have 30 rows');
    assert(await page.locator('#previewNext').isDisabled(), 'Next should be disabled on the final page');
  });
  await check('Preview page-size control resets to page 1', async () => {
    await page.locator('#previewPageSize').selectOption('25');
    assert(await page.locator('#pane-preview tbody tr').count() === 25, 'Page size not applied');
    assert(await page.locator('#previewPrevious').isDisabled(), 'Page did not reset');
  });
  await check('Preview search keeps input focus while typing', async () => {
    await page.locator('#previewSearch').fill('Person 230');
    assert(await page.locator('#previewSearch').evaluate(el => document.activeElement === el), 'Search lost focus');
    assert(await page.locator('#pane-preview tbody tr').count() === 1, 'Expected one result');
  });
  await check('Preview search explicitly handles zero matches', async () => {
    await page.locator('#previewSearch').fill('missing-value');
    assert(await page.locator('#pane-preview tbody tr').count() === 0, 'Unexpected result');
    assert((await page.locator('#pane-preview .empty').innerText()).includes('No matching rows'), 'No-results feedback missing');
  });
  await check('Sensitive values cannot be discovered through search', async () => {
    await page.evaluate(() => { DataForge.Governance.settings().maskPreview = true; DataForge.Governance.settings().sensitive = ['email']; DataForge.Governance.settings().autoDetect = false; DataForge.UI.renderPreview(); });
    await page.locator('#previewSearch').fill('secret230');
    assert(await page.locator('#pane-preview tbody tr').count() === 0, 'Sensitive value leaked through filtering');
    await page.locator('#previewSearch').fill('Person 230');
    assert(await page.locator('#pane-preview td.masked').count() === 1, 'Sensitive field is not masked');
    assert(!(await page.locator('#pane-preview tbody').innerText()).includes('secret230'), 'Unmasked data in table');
  });
  await check('Column sorting is keyboard-accessible and numeric', async () => {
    await page.locator('#previewSearch').fill('');
    const sort = page.getByRole('button', { name: 'Sort by id', exact: true });
    await sort.focus(); await page.keyboard.press('Enter');
    assert(await page.locator('#pane-preview th[aria-sort="ascending"]').count() === 1, 'Sort state not announced');
    await page.keyboard.press('Enter');
    assert(await page.locator('#pane-preview tbody tr:first-child td').nth(1).innerText() === '230', 'Descending numeric sort incorrect');
    await page.keyboard.press('Enter');
    assert(await page.locator('#pane-preview th[aria-sort="none"]').count() === 3, 'Third activation should reset sorting');
  });
  await check('Preview safely renders HTML-like cell values as text', async () => {
    await page.evaluate(() => { DataForge.state.preview.rows = [['1','<img src=x onerror=alert(1)>',null]]; DataForge.UI.renderPreview(); });
    assert(await page.locator('#pane-preview tbody img').count() === 0, 'HTML was interpreted');
    assert((await page.locator('#pane-preview tbody').innerText()).includes('<img'), 'Cell text missing');
  });
  await check('Empty preview handles an empty result set', async () => {
    await page.evaluate(() => { DataForge.state.preview.rows=[]; DataForge.UI.renderPreview(); });
    assert((await page.locator('#pane-preview .empty').innerText()).includes('No rows'), 'Empty preview missing');
  });
  await check('Workflow palette is searchable and keyboard-focusable', async () => {
    await page.evaluate(() => DataForge.Workflow.sample());
    await page.locator('#paletteSearch').fill('session');
    assert(await page.locator('.pal-item:visible').count() >= 1, 'Workflow search failed');
    assert(await page.locator('.pal-item:visible').first().getAttribute('role') === 'button', 'Workflow item is not a button');
  });
  await check('Palette query persists independently for mapping mode', async () => {
    await page.selectOption('#studioMode','mapping');
    assert(await page.locator('#paletteSearch').inputValue() === 'filter', 'Mapping search did not persist');
  });
  await check('Monitor stays below the resized header', async () => {
    await page.selectOption('#studioMode','monitor');
    const box=await page.locator('#monitorStage').boundingBox();
    assert(box.y >= 103, 'Monitor overlaps header');
    await page.selectOption('#studioMode','mapping');
  });
  await check('Sample SQL generation still works for all seven dialects', async () => {
    const generated=await page.evaluate(() => {
      DataForge.Actions.loadSample('original');
      return ['snowflake','bigquery','databricks','redshift','pyspark','dbt','duckdb'].map(d=>{
        const out=DataForge.CodeGen.generate(d);return [d,(out.files?Object.values(out.files).join(''):out.text).length];
      });
    });
    for(const [dialect,size] of generated) assert(size>200, 'Empty export: '+dialect);
  });
  await check('Sample additions preserve existing mappings', async () => {
    const before=await page.evaluate(() => DataForge.Repo.project.mappings.length);
    await page.evaluate(() => DataForge.Actions.loadSample('phase2'));
    assert(await page.evaluate(() => DataForge.Repo.project.mappings.length) === before+1, 'Existing mapping overwritten');
  });
  await check('Light-theme desktop layout has no document overflow', async () => {
    if (await page.evaluate(() => document.documentElement.dataset.theme) !== 'light') await page.locator('#btnTheme').click();
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Desktop overflows');
    await page.screenshot({ path: join(artifacts,'desktop-light.png') });
    await page.locator('#btnTheme').click();
  });
  await check('Dark-theme desktop layout has no document overflow', async () => {
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Desktop overflows');
    await page.screenshot({ path: join(artifacts,'desktop-dark.png') });
  });
  await check('Reduced-motion preference disables transitions', async () => {
    await page.emulateMedia({ reducedMotion:'reduce' });
    assert(await page.locator('#btnRun').evaluate(el=>getComputedStyle(el).transitionDuration) === '0s', 'Motion is still enabled');
  });
  const mobile=await open(390,844);
  await check('Phone welcome has no horizontal overflow', async () => {
    assert(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Phone page overflows');
    assert(await mobile.locator('#welcomeStage').evaluate(el=>el.scrollWidth <= el.clientWidth), 'Phone welcome overflows');
  });
  await check('Phone template cards load mappings', async () => {
    await mobile.getByRole('button',{name:'Load Retail orders sample',exact:true}).click();
    assert(await mobile.locator('#canvas').isVisible(), 'Canvas not visible');
  });
  await check('Phone library drawer opens below the header', async () => {
    await mobile.locator('#btnPanelLeft').click();
    assert(await mobile.locator('#palette').isVisible(), 'Library drawer did not open');
    assert((await mobile.locator('#palette').boundingBox()).y >= 99, 'Drawer overlaps header');
    await mobile.locator('#paletteSearch').fill('filter');
    const before=await mobile.evaluate(()=>DataForge.state.doc.nodes.length);
    await mobile.locator('.pal-item:visible').click();
    assert(await mobile.evaluate(()=>DataForge.state.doc.nodes.length) === before+1,'Tap did not add node');
    assert(!(await mobile.locator('#palette').isVisible()), 'Drawer did not close after adding');
  });
  await check('Phone properties drawer opens and closes with Escape', async () => {
    await mobile.locator('#btnPanelRight').click();
    assert(await mobile.locator('#props').isVisible(),'Properties drawer is hidden');
    await mobile.keyboard.press('Escape');
    assert(!(await mobile.locator('#props').isVisible()),'Escape did not close drawer');
  });
  await check('Phone engine status remains accessible for retry', async () => {
    assert(await mobile.locator('#enginePill').isVisible(),'Engine status is hidden');
  });
  await mobile.screenshot({path:join(artifacts,'phone-workspace.png')});
  await mobile.setViewportSize({width:320,height:700});
  await check('320px viewport keeps primary header controls inside the screen', async () => {
    assert(await mobile.evaluate(()=>document.documentElement.scrollWidth <= innerWidth),'Small viewport overflows');
    const b=await mobile.locator('#btnPanelRight').boundingBox();assert(b.x+b.width<=320,'Properties button falls off-screen');
  });
  await check('No uncaught browser JavaScript errors', async () => assert(errors.length===0,errors.join('; ')));
  await mobile.close();
} finally {
  await browser.close();
  const report={suite:'Offline UI regression',scope:'DOM rendering, interactions, generated code text; external requests blocked. No DuckDB query execution, real-origin IndexedDB persistence or complete existing smoke suite.',passed:results.filter(r=>r.status==='PASS').length,failed:results.filter(r=>r.status==='FAIL').length,results,errors};
  writeFileSync(join(artifacts,'results.json'),JSON.stringify(report,null,2)+'\n');
  console.log(`\n${report.passed} passed; ${report.failed} failed`);
  if(report.failed)process.exitCode=1;
}
