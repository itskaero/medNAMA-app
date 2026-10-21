// Render tools/paint.html to assets/<scene>.jpg:  node tools/paint.js hero closing   (Playwright on NODE_PATH)
const { chromium } = require("playwright");
const path = require("path");

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 2400, height: 1400 } });
  for (const scene of process.argv.slice(2)) {
    await page.goto("file://" + path.resolve(__dirname, "paint.html") + "?scene=" + scene);
    await page.waitForFunction(() => window.DONE === true, null, { timeout: 120000 });
    await page.locator("canvas").screenshot({ path: path.resolve(__dirname, "..", "assets", scene + ".jpg"), type: "jpeg", quality: 84 });
    console.log("painted", scene);
  }
  await browser.close();
})();
