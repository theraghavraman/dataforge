import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdtempSync, writeFileSync, mkdirSync, cpSync, rmSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, join } from 'node:path';
import { createHash } from 'node:crypto';
import { createDataForgeServer } from '../scripts/serve.mjs';

async function host(options, fn) {
  const server = createDataForgeServer(options); server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const base = `http://127.0.0.1:${server.address().port}`;
  try { return await fn(base); } finally { server.closeAllConnections(); await new Promise(r => server.close(r)); }
}
await test('HTML responds with restrictive content type and no-store', () => host({}, async base => {
  const r = await fetch(base); assert.equal(r.status, 200); assert.match(r.headers.get('content-type'), /text\/html/); assert.equal(r.headers.get('cache-control'), 'no-store');
}));
await test('Script hash matches the actual served inline script', () => host({}, async base => {
  const r = await fetch(base), text = await r.text(), csp = r.headers.get('content-security-policy');
  const script = text.match(/<script>([\s\S]*?)<\/script>/)[1];
  assert.ok(csp.includes("'sha256-" + createHash('sha256').update(script).digest('base64') + "'"));
  assert.ok(!csp.split(';').find(x => x.trim().startsWith('script-src')).includes('unsafe-inline'));
}));
await test('Framing and MIME sniffing protections are HTTP headers', () => host({}, async base => {
  const r = await fetch(base); assert.match(r.headers.get('content-security-policy'), /frame-ancestors 'none'/); assert.equal(r.headers.get('x-frame-options'), 'DENY'); assert.equal(r.headers.get('x-content-type-options'), 'nosniff');
}));
await test('Referrer and device-permission restrictions are present', () => host({}, async base => {
  const r = await fetch(base); assert.equal(r.headers.get('referrer-policy'), 'no-referrer'); assert.match(r.headers.get('permissions-policy'), /microphone=\(\)/); assert.match(r.headers.get('permissions-policy'), /geolocation=\(\)/);
}));
await test('Health response distinguishes static serving from execution validation', () => host({}, async base => {
  const data = await (await fetch(base + '/healthz')).json(); assert.equal(data.status, 'ok'); assert.equal(data.service, 'dataforge-static'); assert.equal(data.engineExecutionValidated, false);
}));
await test('HEAD exposes headers but no response body', () => host({}, async base => {
  const r = await fetch(base, { method: 'HEAD' }); assert.equal(r.status, 200); assert.equal(await r.text(), ''); assert.ok(+r.headers.get('content-length') > 10000);
}));
await test('Write methods are refused', () => host({}, async base => {
  for (const method of ['POST','PUT','DELETE','PATCH']) { const r = await fetch(base, { method }); assert.equal(r.status, 405); assert.equal(r.headers.get('allow'), 'GET, HEAD'); }
}));
await test('Project files, source, tests and configuration are not publicly served', () => host({}, async base => {
  for (const path of ['/package.json','/package-lock.json','/src/business.js','/tests/ui.mjs','/deploy/dataforge.service','/.git/config','/my_project.dfproj','/vendor/']) assert.equal((await fetch(base+path)).status, 404, path);
}));
await test('Encoded traversal and unknown routes are refused', () => host({}, async base => {
  for (const path of ['/vendor/duckdb/%2e%2e%2f%2e%2e%2fpackage.json','/%00','/not-real','/vendor/duckdb/../../package.json']) assert.equal((await fetch(base+path)).status, 404, path);
}));
await test('Local-only CSP removes external script and connection hosts', () => host({ network: 'local' }, async base => {
  const r=await fetch(base),csp=r.headers.get('content-security-policy'),html=await r.text();
  assert.ok(!csp.includes('https://')); assert.match(html, /name="dataforge-network" content="local"/); assert.ok(!html.includes('<link rel="preconnect" href="https://cdn.jsdelivr.net"'));
}));
await test('CDN mode permits only the configured runtime hosts in connect-src', () => host({ network: 'cdn' }, async base => {
  const csp=(await fetch(base)).headers.get('content-security-policy');
  assert.match(csp,/connect-src 'self' blob: https:\/\/cdn.jsdelivr.net https:\/\/extensions.duckdb.org;/);
}));
await test('No remote font stylesheet is requested by the HTML', () => host({}, async base => {
  const html=await (await fetch(base)).text(); assert.ok(!/<link[^>]*(?:fonts.googleapis.com|fonts.gstatic.com)/.test(html));
}));
await test('Runtime assets are served with correct MIME types and revalidation', async () => {
  const root=mkdtempSync(join(tmpdir(),'df-serve-'));
  try {
    cpSync(new URL('../index.html',import.meta.url),join(root,'index.html')); cpSync(new URL('../package.json',import.meta.url),join(root,'package.json'));
    mkdirSync(join(root,'vendor/duckdb'),{recursive:true});writeFileSync(join(root,'vendor/duckdb/duckdb-eh.wasm'),Buffer.from([0,97,115,109]));
    await host({root},async base=>{const r=await fetch(base+'/vendor/duckdb/duckdb-eh.wasm');assert.equal(r.status,200);assert.equal(r.headers.get('content-type'),'application/wasm');assert.equal(r.headers.get('cache-control'),'no-cache');});
  } finally {rmSync(root,{recursive:true,force:true});}
});
await test('Runtime symlinks cannot expose files outside the served root', async () => {
  const root=mkdtempSync(join(tmpdir(),'df-link-')),outside=mkdtempSync(join(tmpdir(),'df-outside-'));
  try {
    cpSync(new URL('../index.html',import.meta.url),join(root,'index.html'));cpSync(new URL('../package.json',import.meta.url),join(root,'package.json'));
    mkdirSync(join(root,'vendor/duckdb'),{recursive:true});writeFileSync(join(outside,'private.js'),'secret');symlinkSync(join(outside,'private.js'),join(root,'vendor/duckdb/private.js'));
    await host({root},async base=>assert.equal((await fetch(base+'/vendor/duckdb/private.js')).status,404));
  } finally {rmSync(root,{recursive:true,force:true});rmSync(outside,{recursive:true,force:true});}
});
await test('Invalid deployment network modes fail at startup',()=>assert.throws(()=>createDataForgeServer({network:'typo'}),/must be cdn or local/));
