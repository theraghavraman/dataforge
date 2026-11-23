#!/usr/bin/env node
/** Restricted static host. Not an auth service or an ETL backend. */
import { createServer } from 'node:http';
import { readFileSync, realpathSync, statSync, existsSync } from 'node:fs';
import { resolve, dirname, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';

export function createDataForgeServer({ root = resolve(dirname(fileURLToPath(import.meta.url)), '..'), network = process.env.DATAFORGE_NETWORK || 'cdn' } = {}) {
  if (!['cdn', 'local'].includes(network)) throw new Error('DATAFORGE_NETWORK must be cdn or local.');
  const base = realpathSync(root);
  let html = readFileSync(resolve(base, 'index.html'), 'utf8');
  html = html.replace(/(<meta name="dataforge-network" content=")[^"]*(">)/, `$1${network}$2`);
  if (network === 'local') html = html.replace(/<link[^>]+href="https:\/\/cdn\.jsdelivr\.net"[^>]*>/g, '');
  const hashes = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)].map(m => "'sha256-" + createHash('sha256').update(m[1]).digest('base64') + "'");
  const externals = network === 'cdn' ? ' https://cdn.jsdelivr.net' : '';
  const connections = network === 'cdn' ? ' https://cdn.jsdelivr.net https://extensions.duckdb.org' : '';
  const csp = `default-src 'none'; script-src 'self' ${hashes.join(' ')} 'wasm-unsafe-eval' blob:${externals}; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data: blob:; connect-src 'self' blob:${connections}; base-uri 'none'; form-action 'none'; object-src 'none'; frame-ancestors 'none'`;
  const htmlBytes = Buffer.from(html);
  const version = JSON.parse(readFileSync(resolve(base, 'package.json'), 'utf8')).version;
  const headers = {
    'Content-Security-Policy': csp, 'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'no-referrer', 'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
    'Cross-Origin-Opener-Policy': 'same-origin', 'Cache-Control': 'no-store'
  };
  const mime = { '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8', '.wasm': 'application/wasm', '.json': 'application/json; charset=utf-8' };
  const server = createServer((req, res) => {
    for (const [key,value] of Object.entries(headers)) res.setHeader(key,value);
    const respond = (status, type, bytes) => {
      const body = Buffer.isBuffer(bytes) ? bytes : Buffer.from(bytes);
      res.writeHead(status, { 'Content-Type': type, 'Content-Length': body.length });
      res.end(req.method === 'HEAD' ? undefined : body);
    };
    if (!['GET','HEAD'].includes(req.method)) { res.setHeader('Allow','GET, HEAD'); respond(405,'text/plain; charset=utf-8','Method not allowed\n'); return; }
    try {
      const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname);
      if (pathname === '/' || pathname === '/index.html') { respond(200,'text/html; charset=utf-8',htmlBytes); return; }
      if (pathname === '/healthz') {
        respond(200,'application/json; charset=utf-8',JSON.stringify({ status: 'ok', service: 'dataforge-static', appVersion: version, networkMode: network, localEngineAssetsPresent: ['duckdb.mjs','duckdb-eh.wasm','duckdb-browser-eh.worker.js'].every(f => existsSync(resolve(base,'vendor/duckdb',f))), engineExecutionValidated: false })); return;
      }
      // Expose only runtime assets and the license, never arbitrary files or directory listings.
      if (!/^\/vendor\/duckdb\/[A-Za-z0-9_-]+\.(?:wasm|js|mjs|json)$/.test(pathname) && pathname !== '/LICENSE') { respond(404,'text/plain; charset=utf-8','Not found\n'); return; }
      const path = realpathSync(resolve(base,'.' + pathname));
      if (!path.startsWith(base + sep) || !statSync(path).isFile()) { respond(404,'text/plain; charset=utf-8','Not found\n'); return; }
      const bytes = readFileSync(path);
      res.setHeader('ETag','"'+createHash('sha256').update(bytes).digest('hex')+'"');
      res.setHeader('Cache-Control','no-cache');
      respond(200,mime[extname(path)] || 'text/plain; charset=utf-8',bytes);
    } catch (_) { respond(404,'text/plain; charset=utf-8','Not found\n'); }
  });
  server.requestTimeout = 15000; server.headersTimeout = 10000; server.keepAliveTimeout = 5000;
  return server;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const port = Number(process.env.PORT || 8080), host = process.env.DATAFORGE_HOST || '127.0.0.1';
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('PORT must be an integer from 1 to 65535.');
  const server = createDataForgeServer();
  server.on('error', error => { console.error('DataForge host failed:', error.message); process.exitCode = 1; });
  server.listen(port,host,() => {
    console.log(`DataForge business pilot: http://${host}:${port}`);
    console.log('Single-user local storage. No authentication, server backup or ETL worker is provided.');
    if (!['127.0.0.1','localhost','::1'].includes(host)) console.warn('Network-facing bind: require an authenticated HTTPS reverse proxy before business use.');
  });
  for (const signal of ['SIGINT','SIGTERM']) process.on(signal,() => { server.close(); server.closeIdleConnections(); });
}
