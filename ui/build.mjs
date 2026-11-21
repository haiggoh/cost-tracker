#!/usr/bin/env node
// ARCHIVED abandoned variant (superseded by the pane mod; see git history).
// Deterministic build of the MCP Apps UI resource: ui/dist/panel.html (single file,
// script inlined). Usage: node build.mjs [--check] [--out DIR]
//   --check   build to a temp file and diff against dist/panel.html; exit 1 on drift
//   --out DIR write the single file into DIR instead of dist/
// Env: none. Exits non-zero with a usage line on an unknown flag.
import { build } from "esbuild";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
if (args.includes("--help") || args.includes("-h")) {
  console.log("usage: node build.mjs [--check] [--out DIR]\n" +
    "  builds ui/dist/panel.html from src/cost-panel.ts (deterministic, script inlined)\n" +
    "  --check  exit 1 if dist/panel.html differs from a fresh build\n" +
    "  --out DIR  write panel.html into DIR instead of dist/");
  process.exit(0);
}
const check = args.includes("--check");
const outIdx = args.indexOf("--out");
const outDir = outIdx !== -1 ? args[outIdx + 1] : join(here, "dist");
if (args.some(a => a.startsWith("--") && !["--check", "--out", "--help", "-h"].includes(a))) {
  console.error("unknown flag; usage: node build.mjs [--check] [--out DIR]");
  process.exit(2);
}

const result = await build({
  entryPoints: [join(here, "src", "cost-panel.ts")],
  bundle: true,
  format: "iife",
  minify: true,
  write: false,
  legalComments: "none",
});
const js = result.outputFiles[0].text;
const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>cost-tracker panel</title>
<style>
  body { font: 14px/1.45 system-ui, sans-serif; margin: 12px; color: inherit; }
  h1 { font-size: 15px; margin: 0 0 6px; }
  .amount { font-size: 22px; font-weight: 700; margin: 4px 0; }
  .basis, .meta { opacity: .75; font-size: 12px; margin: 2px 0; }
  .state { font-size: 12px; font-weight: 700; padding: 1px 6px; border-radius: 4px; }
  .state.cap { background: #b91c1c; color: #fff; }
  .state.incomplete { background: #b45309; color: #fff; }
  .state.stale { background: #a16207; color: #fff; }
  .state.error, p.error { color: #b91c1c; }
  table.hosts { border-collapse: collapse; margin: 8px 0; }
  table.hosts td, table.hosts th { border: 1px solid #8884; padding: 2px 8px; text-align: left; }
  .diags { color: #b45309; font-size: 12px; }
  .missing { color: #b45309; font-size: 12px; }
  footer { margin-top: 10px; }
</style>
</head>
<body>
<div id="root"><p>loading snapshot…</p></div>
<script>${js}</script>
</body>
</html>
`;

if (check) {
  const cur = readFileSync(join(here, "dist", "panel.html"), "utf8");
  if (cur !== html) {
    console.error("ui/dist/panel.html drifted from a fresh build; run node build.mjs");
    process.exit(1);
  }
  console.log("ui/dist/panel.html matches a fresh build");
  process.exit(0);
}
mkdirSync(outDir, { recursive: true });
writeFileSync(join(outDir, "panel.html"), html);
console.log(`built ${join(outDir, "panel.html")} (${html.length} bytes)`);
