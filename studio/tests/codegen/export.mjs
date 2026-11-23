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

// A mapping that uses every transformation the samples do not (joiner, router, union, sequence, rank, SQL) and the
// functions that differ most between engines, with awkward values: a quoted comma, an apostrophe, a backslash,
// a semicolon, NULLs and a customer the LEFT JOIN does not find.
const COVERAGE = {
  orders: 'order_id,cust,amount,status,order_date,note\n1,C1,120.5,DELIVERED,2026-01-05,ok\n2,C2,40,DELIVERED,2026-02-11,\n3,C1,15.25,CANCELLED,2026-02-20,late; refund\n4,C3,300,DELIVERED,2026-03-01,"has, comma"\n5,C9,75,DELIVERED,2026-03-15,it\'s fine\n6,C2,,DELIVERED,2026-03-20,back\\slash\n',
  customers: 'cust,name,email,segment\nC1,Ana,ana@x.com,Retail\nC2,Bo,bo-at-y,Corporate\nC3,Cy,cy@z.net,\n'
};
async function buildCoverage([orders, customers]) {
  const D = DataForge, A = D.Actions, S = D.state, sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  D.Repo.createMapping(); S.doc.mapping.name = 'Coverage';
  const add = (t, x, y) => { A.addNode(t, x, y); return S.doc.nodes[S.doc.nodes.length - 1]; };
  const o = add('csv_source', 0, 0), c = add('csv_source', 0, 300), j = add('joiner', 250, 100), e = add('expression', 500, 100), rt = add('router', 750, 100), u = add('union', 1000, 100),
    sq = add('sequence_generator', 1250, 100), rk = add('rank', 1500, 100), ag = add('aggregator', 1250, 350), sql = add('sql_transform', 1500, 350),
    t1 = add('csv_target', 1750, 100), t2 = add('csv_target', 1750, 350), t3 = add('csv_target', 1000, 400);
  A.link(o.id, j.id, null, 'master'); A.link(c.id, j.id, null, 'detail'); A.link(j.id, e.id); A.link(e.id, rt.id);
  j.config.joinType = 'LEFT'; j.config.conditions = [{ master: 'cust', detail: 'cust' }];
  e.config.fields = [{ name: 'month', expression: "strftime(order_date, '%Y-%m')" }, { name: 'age_days', expression: "date_diff('day', order_date, DATE '2026-04-01')" },
    { name: 'valid_email', expression: "regexp_matches(email, '^[^@\\s]+@[^@\\s]+\\.[a-z]+$')" }, { name: 'tag', expression: "concat_ws('|', UPPER(segment), status, note)" },
    { name: 'h', expression: "md5(COALESCE(name,''))" }, { name: 'amt', expression: 'ROUND(COALESCE(amount, 0) * 1.1, 2)' }];
  rt.config.groups = [{ key: 'g1', name: 'BIG', condition: 'amt >= 100' }, { key: 'g2', name: 'SMALL', condition: 'amt < 100' }]; rt.config.multiple = false;
  D.History.commit(); await sleep(300);
  A.link(rt.id, u.id, 'g1'); A.link(rt.id, u.id, 'g2'); A.link(u.id, sq.id); A.link(sq.id, rk.id); A.link(rk.id, t1.id);
  A.link(sq.id, ag.id); A.link(ag.id, sql.id); A.link(sql.id, t2.id); A.link(rt.id, t3.id, 'default');
  Object.assign(sq.config, { outputName: 'sk', start: 100, increment: 10, orderBy: 'order_id' });
  Object.assign(rk.config, { function: 'DENSE_RANK', column: 'amt', direction: 'DESC', groupBy: ['month'], n: 2, position: 'top', outputName: 'rnk' });
  ag.config.groupBy = ['month']; ag.config.fields = [{ name: 'orders', expression: 'COUNT(*)' }, { name: 'total', expression: 'SUM(amt)' }, { name: 'max_sk', expression: 'MAX(sk)' }];
  sql.config.sql = "SELECT month, orders, total, CASE WHEN total > 200 THEN 'high' ELSE 'normal' END AS band\nFROM input\nWHERE orders > 0";
  Object.assign(t1, { name: 'TGT_ranked' }); Object.assign(t2, { name: 'TGT_monthly' }); Object.assign(t3, { name: 'TGT_unrouted' });
  t1.config.fileName = 'ranked.csv'; t2.config.fileName = 'monthly.csv'; t3.config.fileName = 'unrouted.csv';
  D.History.commit();
  await A.attachFile(o.id, new File([orders], 'orders.csv')); await A.attachFile(c.id, new File([customers], 'customers.csv')); await sleep(1500);
}

for (const sample of ['original', 'phase2', 'phase3', 'coverage']) {
  if (sample === 'coverage') await page.evaluate(buildCoverage, [COVERAGE.orders, COVERAGE.customers]);
  else { await page.click('#btnSampleMenu'); await page.click(`[data-sample="${sample}"]`); await page.waitForTimeout(600); }
  await page.click('#btnRun'); await page.waitForTimeout(300);
  await page.waitForFunction(() => !DataForge.state.running, null, { timeout: 60000 }); await page.waitForTimeout(300);
  const dump = await page.evaluate(async () => {
    const D = DataForge, b64 = (u8) => { let s = ''; for (let i = 0; i < u8.length; i += 8192) s += String.fromCharCode(...u8.subarray(i, i + 8192)); return btoa(s); };
    const code = {};
    for (const d of ['snowflake', 'pyspark', 'dbt', 'duckdb', 'bigquery', 'databricks', 'redshift']) { const g = D.CodeGen.generate(d); code[d] = { text: g.text || null, files: g.files || null }; }
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
  for (const d of ['bigquery', 'databricks', 'redshift']) put(d + '.sql', dump.code[d].text);
  for (const [name, body] of Object.entries(dump.code.dbt.files)) put('dbt/' + name, body);
  console.log(`exported ${sample}: ${dump.sources.length} source file(s), ${dump.outputs.length} target file(s)`);
}
await browser.close(); server.close();
