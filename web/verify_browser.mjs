import { chromium } from "playwright";
import { readFileSync, mkdirSync } from "fs";

// creds from project .env (SMOKE_EMAIL / SMOKE_PASSWORD)
const env = Object.fromEntries(
  readFileSync("../.env", "utf8").split("\n").filter((l) => l.includes("="))
    .map((l) => { const i = l.indexOf("="); return [l.slice(0, i).trim(), l.slice(i + 1).trim()]; })
);
const EMAIL = env.SMOKE_EMAIL, PASS = env.SMOKE_PASSWORD;
const BASE = "http://localhost:5180";
const OUT = "/tmp/nha_verify";
mkdirSync(OUT, { recursive: true });

const log = (...a) => console.log(...a);
const consoleErrors = [];

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
page.on("pageerror", (e) => consoleErrors.push("PAGEERROR: " + e.message));
// network trace for API calls (ground truth for the write paths)
page.on("request", (r) => { if (r.url().includes(":8000")) log(`  → ${r.method()} ${r.url().replace("http://localhost:8000","")}`); });
page.on("requestfailed", (r) => { if (r.url().includes(":8000")) log(`  ✗ FAILED ${r.method()} ${r.url().replace("http://localhost:8000","")} : ${r.failure()?.errorText}`); });
page.on("response", async (r) => { if (r.url().includes(":8000") && r.request().method() !== "OPTIONS") { let b = ""; try { b = (await r.text()).slice(0, 160); } catch {} log(`  ← ${r.status()} ${r.request().method()} ${r.url().replace("http://localhost:8000","")} ${b}`); } });

async function shot(name) { await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true }); log(`  shot: ${name}.png`); }
async function go(path, name) {
  // BrowserRouter + Vite SPA fallback: deep-link with a real path. Supabase
  // session persists in localStorage across reloads, so we stay signed in.
  await page.goto(BASE + path, { waitUntil: "networkidle" }).catch(() => {});
  await page.waitForTimeout(900);
  await shot(name);
}

try {
  // 1. Login
  log("STEP login");
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.waitForTimeout(600);
  await shot("01-login");
  await page.fill('input[type="email"]', EMAIL);
  await page.fill('input[type="password"]', PASS);
  await page.click('button[type="submit"]');
  await page.waitForTimeout(2500); // auth + manager/teams resolve
  await shot("02-after-login");
  const url1 = page.url();
  log("  url after login:", url1);

  // 2. Page click-through (hash router)
  for (const [path, name] of [
    ["/dashboard", "03-dashboard"],
    ["/my-teams", "04-my-teams"],
    ["/teams", "05-teams"],
    ["/standings", "06-standings"],
    ["/leaderboard", "07-leaders"],
    ["/schedule", "08-schedule"],
    ["/draft", "09-draft"],
    ["/trades", "10-trades"],
    ["/commissioner", "11-commissioner"],
  ]) {
    log("STEP visit", path);
    await go(path, name);
  }

  // 3. Roster + lineup editor (Boston) — slugs are capitalized in the DB
  log("STEP roster boston");
  await go("/teams/Boston", "12-roster-boston");
  // try a lineup edit: click first "▼" move control then Save
  const moveBtns = await page.locator('button[title="Move down"]').count();
  log("  move-down controls present:", moveBtns);
  if (moveBtns > 0) {
    await page.locator('button[title="Move down"]').first().click();
    await page.waitForTimeout(300);
    const saveBtn = page.getByText("Save lineup", { exact: true });
    if (await saveBtn.count()) {
      const respP = page.waitForResponse(
        (r) => r.url().includes("/arrangement") && r.request().method() === "PUT",
        { timeout: 30000 }
      );
      await saveBtn.first().click();
      const resp = await respP;
      log(`  PUT arrangement -> ${resp.status()} ${(await resp.text()).slice(0, 120)}`);
      await page.waitForTimeout(800);
      await shot("13-roster-after-save");
      log("  lineup save toast present:", (await page.getByText("Lineup saved.").count()) > 0);
    } else log("  NO Save button found");
  } else log("  NO move controls (not editable?)");

  // 4. Trades: propose an internal trade Boston -> Seattle
  log("STEP propose trade");
  await go("/trades", "14-trades-page");
  const sel = page.locator("select").first();
  if (await sel.count()) {
    const opts = await sel.locator("option").allTextContents();
    log("  trade-with options sample:", opts.slice(0, 6));
    // pick Seattle (an owned team -> internal) if present, else first non-empty
    const seattle = opts.find((o) => /Seattle/i.test(o));
    await sel.selectOption({ label: seattle || opts.find((o) => o && !/select/i.test(o)) });
    await page.waitForTimeout(1200);
    await shot("15-trade-team-selected");
    // select one checkbox on each side if available
    const checks = page.locator('input[type="checkbox"]');
    const nChecks = await checks.count();
    log("  player checkboxes:", nChecks);
    if (nChecks > 0) await checks.first().click();
    const propose = page.getByText(/Propose/i).first();
    if (await propose.count()) {
      const respP = page.waitForResponse(
        (r) => r.url().endsWith("/trades") && r.request().method() === "POST",
        { timeout: 30000 }
      );
      await propose.click();
      const resp = await respP;
      log(`  POST trades -> ${resp.status()} ${(await resp.text()).slice(0, 160)}`);
      await page.waitForTimeout(1000);
      await shot("16-after-propose");
      const toastInternal = await page.getByText(/Internal trade created|Trade proposed/i).count();
      log("  propose toast present:", toastInternal > 0);
    } else log("  NO propose button");
  } else log("  NO trade team selector found");

  log("CONSOLE_ERRORS:", JSON.stringify(consoleErrors.slice(0, 20), null, 2));
  log("DONE");
} catch (e) {
  log("SCRIPT_ERROR:", e.message);
  await shot("99-error");
} finally {
  await browser.close();
}
