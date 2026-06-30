import { chromium } from "playwright";
import { readFileSync, mkdirSync } from "fs";

const env = Object.fromEntries(
  readFileSync("../.env", "utf8").split("\n").filter((l) => l.includes("="))
    .map((l) => { const i = l.indexOf("="); return [l.slice(0, i).trim(), l.slice(i + 1).trim()]; })
);
const EMAIL = env.SMOKE_EMAIL, PASS = env.SMOKE_PASSWORD;
const BASE = "https://oliverhvidsten.github.io/handball";   // live Pages site (HashRouter)
const OUT = "/tmp/nha_prod";
mkdirSync(OUT, { recursive: true });

const log = (...a) => console.log(...a);
const consoleErrors = [];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
page.on("pageerror", (e) => consoleErrors.push("PAGEERROR: " + e.message));
page.on("response", async (r) => {
  if (r.url().includes("nha-api.onrender.com") && r.request().method() !== "OPTIONS") {
    let b = ""; try { b = (await r.text()).slice(0, 140); } catch {}
    log(`  ← ${r.status()} ${r.request().method()} ${r.url().split("onrender.com")[1]} ${b}`);
  }
});

const shot = async (n) => { await page.screenshot({ path: `${OUT}/${n}.png`, fullPage: true }); log(`  shot ${n}`); };
const go = async (hash, n) => { await page.goto(`${BASE}/#${hash}`, { waitUntil: "networkidle" }).catch(() => {}); await page.waitForTimeout(1200); await shot(n); };

try {
  log("STEP login (live Supabase)");
  await page.goto(`${BASE}/`, { waitUntil: "networkidle" });
  await page.waitForTimeout(800);
  await shot("01-login");
  await page.fill('input[type="email"]', EMAIL);
  await page.fill('input[type="password"]', PASS);
  await page.click('button[type="submit"]');
  await page.waitForTimeout(3000);
  await shot("02-dashboard");
  log("  url:", page.url());

  for (const [h, n] of [["/standings", "03-standings"], ["/teams", "04-teams"], ["/trades", "05-trades"]]) {
    log("STEP", h); await go(h, n);
  }

  // Lineup save on Boston (reorder -> save -> restore), against the live Render API
  log("STEP lineup save (Boston) via Render API");
  await go("/teams/Boston", "06-roster");
  const orig = await page.locator('button[title="Move down"]').count();
  log("  move controls:", orig);
  if (orig > 0) {
    await page.locator('button[title="Move down"]').first().click();
    const r1 = page.waitForResponse((r) => r.url().includes("/arrangement") && r.request().method() === "PUT", { timeout: 70000 });
    await page.getByText("Save lineup", { exact: true }).click();
    const resp1 = await r1;
    log(`  PUT arrangement -> ${resp1.status()} ${(await resp1.text()).slice(0,80)}`);
    await page.waitForTimeout(1500); await shot("07-after-save");
    // restore original order: move the same player back up, save again
    await page.locator('button[title="Move up"]').nth(1).click();
    const r1b = page.waitForResponse((r) => r.url().includes("/arrangement") && r.request().method() === "PUT", { timeout: 70000 });
    await page.getByText("Save lineup", { exact: true }).click();
    await r1b; log("  restored original lineup");
  }

  // Internal trade propose (Boston->Seattle) then cancel, against the live API
  log("STEP trade propose+cancel via Render API");
  await go("/trades", "08-trades");
  const sel = page.locator("select").first();
  const opts = await sel.locator("option").allTextContents();
  const seattle = opts.find((o) => /Seattle/i.test(o));
  await sel.selectOption({ label: seattle || opts.find((o) => o && !/select/i.test(o)) });
  await page.waitForTimeout(1500);
  const checks = page.locator('input[type="checkbox"]');
  if (await checks.count() > 0) await checks.first().click();
  const r2 = page.waitForResponse((r) => r.url().endsWith("/trades") && r.request().method() === "POST", { timeout: 70000 });
  await page.getByText(/Propose/i).first().click();
  const resp2 = await r2;
  const body = await resp2.text();
  log(`  POST trades -> ${resp2.status()} ${body.slice(0,120)}`);
  await page.waitForTimeout(1500); await shot("09-after-propose");
  // self-clean: cancel the trade we just made
  const cancelBtn = page.getByText("Cancel", { exact: true }).first();
  if (await cancelBtn.count()) {
    const r3 = page.waitForResponse((r) => /\/trades\/.+\/cancel/.test(r.url()) && r.request().method() === "POST", { timeout: 70000 });
    await cancelBtn.click();
    const resp3 = await r3; log(`  POST cancel -> ${resp3.status()} ${(await resp3.text()).slice(0,80)}`);
    await page.waitForTimeout(1200); await shot("10-after-cancel");
  }

  log("CONSOLE_ERRORS:", JSON.stringify(consoleErrors.slice(0, 15)));
  log("DONE");
} catch (e) {
  log("SCRIPT_ERROR:", e.message); await shot("99-error");
} finally { await browser.close(); }
