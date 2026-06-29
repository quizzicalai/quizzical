// Brand-recolor pipeline for the Q&A image-enrichment prototype.
//
// Sources REAL open icons from the installed `lucide-static` package
// (ISC license — even more permissive than MIT; recolor + redistribute OK)
// and emits brand-colored two-tone SVGs in the Quafel palette, plus a
// THIRD-PARTY-ICONS.md attribution manifest (license hygiene, per skeptic).
//
// Style (matches specifications/IMAGE-ENRICHMENT-PLAN.md §3 + Logo.tsx):
//   24x24 viewBox, 2px stroke, round caps/joins, two-tone:
//   stroke = brand token, optional soft wash backing.
//
// Palette per §2 (brand primaries): sea-blue #0079AE primary, indigo
// #4F46E5, amber #D97706, slate neutrals. We assign a palette VARIANT per
// icon by semantic role (the catalog `concept` decides), so the set reads
// as one cohesive system rather than 100 random colors.
//
// Run:  node recolor.mjs
// Out:  recolored/<id>.svg, brand-grid.html, THIRD-PARTY-ICONS.md

import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROTO = join(__dirname, "..");
const LUCIDE = join(__dirname, "node_modules", "lucide-static", "icons");
const OUT = join(__dirname, "recolored");
const catalog = JSON.parse(
  readFileSync(join(PROTO, "data", "icon_catalog.json"), "utf8")
).icons;

// ---- Brand palette (from §2 of the plan) -------------------------------
const PALETTE = {
  sea:    { stroke: "#0079AE", wash: "#D6EEF6" }, // PRIMARY — default Q icons
  indigo: { stroke: "#4F46E5", wash: "#E0E7FF" }, // "smart/abstract" topics
  amber:  { stroke: "#D97706", wash: "#FEF3C7" }, // "fun/energy" topics
  slate:  { stroke: "#334155", wash: "#E2E8F0" }, // neutral answer-option icons
};

// Assign a palette variant by concept keyword so the set is cohesive +
// semantically meaningful (science/logic -> indigo, fun/food/celebration ->
// amber, neutral objects -> slate, everything else -> sea primary).
const INDIGO = /science|chem|phys|biolog|genet|math|brain|think|logic|programming|computer|robot|astronom|idea|puzzle|strateg|chess|education|code/i;
const AMBER  = /fun|party|celebrat|cake|dessert|ice cream|pizza|food|wine|coffee|music|guitar|game|gift|happy|fire|passion|trophy|winning|medal/i;
const SLATE  = /tool|build|construction|repair|settings|engineering|machine|clock|calendar|key|lock|search|phone|mail|chat|user|people|shopping|clothing|cut/i;

function variantFor(concept) {
  if (INDIGO.test(concept)) return "indigo";
  if (AMBER.test(concept)) return "amber";
  if (SLATE.test(concept)) return "slate";
  return "sea";
}

// ---- Recolor one SVG ---------------------------------------------------
// Lucide SVGs are pure line-art with stroke="currentColor". We:
//   1. add a soft rounded wash backing rect (the two-tone fill)
//   2. set the stroke to the brand token
//   3. keep 2px stroke + round caps (already lucide's defaults)
function recolor(svg, variant) {
  const { stroke, wash } = PALETTE[variant];
  // Strip the license comment + class so we control styling cleanly.
  let s = svg.replace(/<!--[\s\S]*?-->/g, "").replace(/\sclass="[^"]*"/g, "");
  // Force brand stroke (replace the literal currentColor on the root svg).
  s = s.replace(/stroke="currentColor"/g, `stroke="${stroke}"`);
  // Insert a two-tone wash backing as the FIRST child of <svg> — a soft
  // rounded square behind the glyph. Decorative; the line-art reads on top.
  const backing = `\n  <rect x="1.5" y="1.5" width="21" height="21" rx="5" fill="${wash}" stroke="none" />`;
  s = s.replace(/(<svg[^>]*>)/, `$1${backing}`);
  return s.trim() + "\n";
}

// ---- Build -------------------------------------------------------------
if (!existsSync(OUT)) mkdirSync(OUT, { recursive: true });

const gridCells = [];
const manifestRows = [];
let made = 0,
  missing = [];

for (const icon of catalog) {
  const src = join(LUCIDE, `${icon.lucide}.svg`);
  if (!existsSync(src)) {
    missing.push(`${icon.id} (${icon.lucide})`);
    continue;
  }
  const variant = variantFor(icon.concept);
  const out = recolor(readFileSync(src, "utf8"), variant);
  writeFileSync(join(OUT, `${icon.id}.svg`), out, "utf8");
  made++;
  gridCells.push(
    `<figure class="cell ${variant}"><div class="ico">${out}</div>` +
      `<figcaption>${icon.id}<span>${variant}</span></figcaption></figure>`
  );
  manifestRows.push(
    `| \`${icon.id}\` | \`lucide:${icon.lucide}\` | Lucide | ISC | ${variant} |`
  );
}

// ---- Grid HTML (for the recolored-icon-grid screenshot) ----------------
const counts = catalog.reduce((m, i) => {
  const v = variantFor(i.concept);
  m[v] = (m[v] || 0) + 1;
  return m;
}, {});
const grid = `<!doctype html><html><head><meta charset="utf-8">
<title>Quafel brand-recolored icon grid</title>
<style>
  :root{--sea:#0079AE;--indigo:#4F46E5;--amber:#D97706;--slate:#334155}
  body{margin:0;background:#F8FAFC;font-family:ui-sans-serif,system-ui,sans-serif;color:#0F172A}
  header{padding:28px 32px 8px}
  h1{margin:0;font-size:22px;letter-spacing:-.01em}
  .sub{color:#475569;font-size:13px;margin-top:6px;max-width:840px;line-height:1.5}
  .legend{display:flex;gap:16px;margin:14px 0 0;flex-wrap:wrap;font-size:12px;color:#334155}
  .chip{display:inline-flex;align-items:center;gap:6px}
  .dot{width:12px;height:12px;border-radius:3px;display:inline-block}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(108px,1fr));gap:14px;padding:24px 32px 48px}
  .cell{margin:0;background:#fff;border:1px solid #E2E8F0;border-radius:14px;padding:12px 8px;text-align:center;box-shadow:0 1px 2px rgba(15,23,42,.04)}
  .ico svg{width:40px;height:40px}
  figcaption{margin-top:8px;font-size:11px;color:#334155;line-height:1.3}
  figcaption span{display:block;font-size:9px;text-transform:uppercase;letter-spacing:.06em;color:#94A3B8;margin-top:2px}
</style></head><body>
<header>
  <h1>Quafel brand-recolored clipart — ${made} icons (Lucide · ISC), one cohesive two-tone system</h1>
  <p class="sub">Real open-source line icons recolored to the brand palette by <code>recolor.mjs</code>.
  24×24 grid, 2px round stroke (matching <code>Logo.tsx</code>), soft two-tone wash backing.
  Palette variant is assigned by semantic role so science→indigo, fun/food→amber,
  tools/UI→slate, everything else→sea-blue primary.</p>
  <div class="legend">
    <span class="chip"><span class="dot" style="background:#0079AE"></span>sea primary (${counts.sea||0})</span>
    <span class="chip"><span class="dot" style="background:#4F46E5"></span>indigo / smart (${counts.indigo||0})</span>
    <span class="chip"><span class="dot" style="background:#D97706"></span>amber / fun (${counts.amber||0})</span>
    <span class="chip"><span class="dot" style="background:#334155"></span>slate / neutral (${counts.slate||0})</span>
  </div>
</header>
<div class="grid">${gridCells.join("")}</div>
</body></html>`;
writeFileSync(join(__dirname, "brand-grid.html"), grid, "utf8");

// ---- Attribution manifest (license hygiene) ----------------------------
const manifest = `# Third-party icons used in the Q&A image-enrichment prototype

All icons below are sourced from **Lucide** (https://lucide.dev), distributed
via the \`lucide-static\` npm package under the **ISC License** (a permissive
MIT-equivalent that allows recoloring + redistribution). Recoloring to the
Quafel brand palette produces derivative works; the ISC notice is retained
below as required.

> ISC License — Copyright (c) for portions of Lucide are held by Cole Bemis
> 2013-2022 as part of Feather (MIT). All other copyright (c) for Lucide are
> held by Lucide Contributors 2022. Permission to use, copy, modify, and/or
> distribute this software for any purpose with or without fee is hereby
> granted, provided the above copyright notice and this permission notice
> appear in all copies.

In production the same pipeline would additionally pull Tabler (MIT),
Material Symbols (Apache-2.0 — retain LICENSE + NOTICE), MDI (Apache-2.0)
and Phosphor (MIT) and emit a per-set NOTICE block here automatically.

| icon id | source name | set | license | brand variant |
|---|---|---|---|---|
${manifestRows.join("\n")}
`;
writeFileSync(join(__dirname, "THIRD-PARTY-ICONS.md"), manifest, "utf8");

console.log(`recolored ${made}/${catalog.length} icons -> ${OUT}`);
console.log(`variants:`, counts);
if (missing.length) console.log(`MISSING lucide names (need remap):`, missing.join(", "));
console.log(`wrote brand-grid.html + THIRD-PARTY-ICONS.md`);
