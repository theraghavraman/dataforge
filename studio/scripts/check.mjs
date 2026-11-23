import { readFileSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
const root=join(dirname(fileURLToPath(import.meta.url)),'..'), html=readFileSync(join(root,'index.html'),'utf8');
const temp=mkdtempSync(join(tmpdir(),'dataforge-check-'));
try {
 let i=0;
 for (const m of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
  const hash="'sha256-"+createHash('sha256').update(m[1]).digest('base64')+"'";
  if (!html.includes(hash)) throw new Error('CSP hash is stale; run npm run build:business.');
  const p=join(temp,`inline-${i++}.js`);writeFileSync(p,m[1]);execFileSync(process.execPath,['--check',p],{stdio:'inherit'});
 }
 console.log(`PASS: ${i} inline script(s) parse and match their CSP hash.`);
} finally {rmSync(temp,{recursive:true,force:true});}
