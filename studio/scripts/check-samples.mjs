#!/usr/bin/env node
// Structural check of samples/ (no browser): the catalog parses, every listed mapping is a DataForge 1.0 mapping,
// links point at existing nodes, every source file is in samples/source and every target file is in samples/target.
// tests/samples.mjs runs the mappings and compares their output with samples/target.
import { readFileSync, existsSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', 'samples');
const safe = (name) => typeof name === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$/.test(name) && !name.includes('..');
const SOURCES = ['csv_source', 'json_source', 'parquet_source', 'excel_source'];
const FILE_TARGETS = ['csv_target', 'json_target', 'parquet_target', 'excel_target', 'reject_target'];
const problems = [];
const fail = (message) => problems.push(message);

const catalog = JSON.parse(readFileSync(join(root, 'catalog.json'), 'utf8'));
if (catalog.format !== 'dataforge-samples' || catalog.version !== 1 || !Array.isArray(catalog.samples) || !catalog.samples.length) fail('catalog.json: not a version 1 dataforge-samples catalog with samples');
const used = { source: new Set(), target: new Set() }, ids = new Set();
for (const entry of catalog.samples || []) {
  const where = `catalog.json "${entry.id}"`;
  if (!entry.id || ids.has(entry.id)) fail(`${where}: missing or duplicate id`); ids.add(entry.id);
  if (!entry.title) fail(`${where}: missing title`);
  if (!safe(entry.mapping) || !existsSync(join(root, 'mappings', entry.mapping))) { fail(`${where}: mapping file mappings/${entry.mapping} not found`); continue; }
  const doc = JSON.parse(readFileSync(join(root, 'mappings', entry.mapping), 'utf8')), at = `mappings/${entry.mapping}`;
  if (doc.version !== '1.0' || !doc.mapping?.name || !Array.isArray(doc.nodes) || !Array.isArray(doc.links)) { fail(`${at}: not a DataForge 1.0 mapping`); continue; }
  const nodeIds = new Set(doc.nodes.map((n) => n.id));
  if (nodeIds.size !== doc.nodes.length) fail(`${at}: duplicate node ids`);
  for (const l of doc.links) if (!nodeIds.has(l.from) || !nodeIds.has(l.to)) fail(`${at}: link ${l.id} points at a missing node`);
  const sources = doc.nodes.filter((n) => SOURCES.includes(n.type)), targets = doc.nodes.filter((n) => FILE_TARGETS.includes(n.type));
  if (!sources.length || !targets.length) fail(`${at}: needs at least one source and one file target`);
  for (const n of sources) {
    const f = n.config?.fileName;
    if (!safe(f) || !existsSync(join(root, 'source', f))) fail(`${at}: ${n.name} reads source/${f}, which does not exist`); else used.source.add(f);
  }
  for (const n of targets) {
    const f = n.config?.fileName;
    if (!safe(f) || !existsSync(join(root, 'target', f))) fail(`${at}: ${n.name} writes ${f}, but target/${f} does not exist (run npm run test:samples -- --write)`); else used.target.add(f);
  }
}
for (const folder of ['source', 'target']) for (const f of readdirSync(join(root, folder))) {
  if (f.startsWith('.')) continue;
  if (!used[folder].has(f)) fail(`${folder}/${f} is not used by any mapping in catalog.json`);
  if (statSync(join(root, folder, f)).size > 5 * 1024 * 1024) fail(`${folder}/${f} is larger than the 5 MB the studio will fetch`);
}
if (problems.length) { console.error(problems.map((p) => 'FAIL: ' + p).join('\n')); process.exit(1); }
console.log(`PASS: ${catalog.samples.length} sample mappings, ${used.source.size} source files and ${used.target.size} target files are consistent.`);
