#!/usr/bin/env node
/**
 * Fails CI if any built JS/CSS chunk exceeds MAX_CHUNK_KB.
 * Run after `vite build`.
 */
import { readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const MAX_CHUNK_KB = Number(process.env.MAX_CHUNK_KB ?? 600);
const distAssets = join(process.cwd(), 'dist', 'assets');

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const p = join(dir, entry);
    const s = statSync(p);
    if (s.isDirectory()) out.push(...walk(p));
    else out.push({ path: p, size: s.size });
  }
  return out;
}

let files;
try {
  files = walk(distAssets);
} catch (err) {
  console.error(`bundle-size: cannot read ${distAssets}: ${err.message}`);
  process.exit(2);
}

const offenders = [];
for (const f of files) {
  if (!/\.(js|css)$/.test(f.path)) continue;
  const kb = f.size / 1024;
  console.log(`  ${(kb).toFixed(1).padStart(8)} KB  ${f.path.replace(process.cwd(), '.')}`);
  if (kb > MAX_CHUNK_KB) offenders.push({ path: f.path, kb });
}

if (offenders.length > 0) {
  console.error(`\nbundle-size: ${offenders.length} chunk(s) exceed ${MAX_CHUNK_KB} KB:`);
  for (const o of offenders) console.error(`  ${o.kb.toFixed(1)} KB  ${o.path}`);
  process.exit(1);
}

console.log(`\nbundle-size: all chunks within ${MAX_CHUNK_KB} KB budget.`);
