// Evaluate every mcqs/**/*.js in a sandbox (no network, no fs) and dump its data as JSON.
// Usage: node dump_mcqs.js <mcqs root> <out.json>
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const [root, out] = process.argv.slice(2);
const files = [];
(function walk(d) {
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, e.name);
    if (e.isDirectory()) walk(p);
    else if (e.name.endsWith(".js")) files.push(p);
  }
})(root);

const result = [];
for (const f of files.sort()) {
  const code = fs.readFileSync(f, "utf8");
  const sandbox = { window: {}, console: { log() {}, warn() {}, error() {} } };
  sandbox.globalThis = sandbox;
  const ctx = vm.createContext(sandbox);
  // Top-level const/let bindings are not properties of the context: collect them explicitly.
  const names = [...code.matchAll(/^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=/gm)].map((m) => m[1]);
  try {
    const extra = vm.runInContext(`${code}\n;({${names.map((n) => `${JSON.stringify(n)}: typeof ${n} === "undefined" ? undefined : ${n}`).join(",")}})`, ctx, { timeout: 5000 });
    result.push({ file: path.relative(root, f).split(path.sep).join("/"), window: sandbox.window, locals: extra });
  } catch (e) {
    result.push({ file: path.relative(root, f).split(path.sep).join("/"), error: String(e).slice(0, 300) });
  }
}
fs.writeFileSync(out, JSON.stringify(result));
console.log(`dumped ${result.length} files, errors: ${result.filter((r) => r.error).length}`);
