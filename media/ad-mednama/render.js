// Render every frame of ad.html to JPEGs:  node render.js <outDir> [fps] [every]
// `every` > 1 renders only every n-th frame (for quick contact sheets). Needs Playwright (NODE_PATH or local).
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

(async () => {
  const out = process.argv[2];
  const fps = Number(process.argv[3] || 30);
  const every = Number(process.argv[4] || 1);
  const tl = JSON.parse(fs.readFileSync(path.join(__dirname, "timeline.json"), "utf8"));
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ args: ["--allow-file-access-from-files"] });   // the end card draws a local image
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1 });
  page.on("pageerror", (e) => { console.error("pageerror", e.message); process.exitCode = 1; });
  await page.goto("file://" + path.join(__dirname, "ad.html").replace(/\\/g, "/"));
  await page.evaluate(async (t) => { window.setTimeline(t); await window.ready(); }, tl);
  const n = Math.round(tl.duration * fps);
  const t0 = Date.now();
  for (let i = 0; i < n; i += every) {
    const data = await page.evaluate((t) => { window.renderAt(t); return document.getElementById("c").toDataURL("image/jpeg", 0.93); }, i / fps);
    fs.writeFileSync(path.join(out, `${String(i).padStart(4, "0")}.jpg`), Buffer.from(data.split(",")[1], "base64"));
    if (i % 150 === 0) console.log(`frame ${i}/${n} (${((Date.now() - t0) / 1000).toFixed(0)}s)`);
  }
  await browser.close();
  console.log(`done ${n} frames`);
})();
