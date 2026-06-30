import { chromium } from "playwright";
import { readFileSync, mkdirSync } from "fs";

// creds from project .env (SMOKE_EMAIL / SMOKE_PASSWORD)
const env = Object.fromEntries(
  readFileSync("../.env", "utf8").split("\n").filter((l) => l.includes("="))
    .map((l) => { const i = l.indexOf("="); return [l.slice(0, i).trim(), l.slice(i + 1).trim()]; })
);
const EMAIL = env.SMOKE_EMAIL, PASS = env.SMOKE_PASSWORD;
const TEMP = PASS + "_tmp9"; // temporary password used only during this run
const BASE = "http://localhost:5180";
const OUT = "/tmp/nha_verify_account";
mkdirSync(OUT, { recursive: true });

const log = (...a) => console.log(...a);
const consoleErrors = [];

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
page.on("pageerror", (e) => consoleErrors.push("PAGEERROR: " + e.message));

async function shot(name) { await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true }); log(`  shot: ${name}.png`); }

// Fill the change-password form and submit. Returns the visible result text.
async function changePassword(current, next) {
  await page.fill('input[autocomplete="current-password"]', current);
  const newFields = page.locator('input[autocomplete="new-password"]');
  await newFields.nth(0).fill(next);
  await newFields.nth(1).fill(next);
  await page.getByRole("button", { name: /Update password/i }).click();
  await page.waitForTimeout(2500); // reauth + updateUser round-trips
}

try {
  // 1. Login
  log("STEP login as", EMAIL);
  await page.goto(BASE, { waitUntil: "networkidle" });
  await page.waitForTimeout(600);
  await page.fill('input[type="email"]', EMAIL);
  await page.fill('input[type="password"]', PASS);
  await page.click('button[type="submit"]');
  await page.waitForTimeout(2500);
  log("  url after login:", page.url());

  // 2. Reach the Account page by clicking the email in the top bar
  log("STEP open account via top-bar email");
  const emailNav = page.getByTitle("Account settings");
  log("  email-as-nav present:", (await emailNav.count()) > 0);
  if (await emailNav.count()) await emailNav.first().click();
  else await page.goto(BASE + "/account", { waitUntil: "networkidle" });
  await page.waitForTimeout(900);
  log("  url:", page.url());
  await shot("01-account");

  // 3. Profile card shows the signed-in email + role
  log("STEP profile card");
  log("  shows email:", (await page.getByText(EMAIL).count()) > 0);
  log("  shows role:", (await page.getByText(/Manager|Commissioner/).count()) > 0);

  // 4. Validation: button disabled until valid; mismatch + too-short flagged
  log("STEP validation");
  const updateBtn = page.getByRole("button", { name: /Update password/i });
  log("  update disabled initially:", await updateBtn.isDisabled());
  await page.fill('input[autocomplete="current-password"]', PASS);
  const newFields = page.locator('input[autocomplete="new-password"]');
  await newFields.nth(0).fill("short");           // < 8 chars
  await newFields.nth(1).fill("short");
  await page.waitForTimeout(200);
  log("  update disabled (too short):", await updateBtn.isDisabled());
  await newFields.nth(0).fill(TEMP);
  await newFields.nth(1).fill(TEMP + "X");         // mismatch
  await page.waitForTimeout(200);
  log("  update disabled (mismatch):", await updateBtn.isDisabled());
  await shot("02-validation");

  // 5. Wrong current password -> error, no change
  log("STEP wrong current password");
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForTimeout(800);
  await changePassword("definitely-not-the-password", TEMP);
  const wrongErr = await page.getByText(/Current password is incorrect/i).count();
  log("  'incorrect current password' shown:", wrongErr > 0);
  await shot("03-wrong-current");

  // 6. Success path: PASS -> TEMP
  log("STEP change password (PASS -> TEMP)");
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForTimeout(800);
  await changePassword(PASS, TEMP);
  const ok1 = await page.getByText(/password has been updated/i).count();
  log("  success alert shown:", ok1 > 0);
  await shot("04-changed");

  // 7. Prove it took: sign out, sign in with TEMP
  log("STEP verify new password works (sign out / sign in with TEMP)");
  await page.getByRole("button", { name: /Sign out/i }).click();
  await page.waitForTimeout(1500);
  await page.fill('input[type="email"]', EMAIL);
  await page.fill('input[type="password"]', TEMP);
  await page.click('button[type="submit"]');
  await page.waitForTimeout(2500);
  const loggedInWithTemp = !/login/i.test(page.url()) && (await page.getByTitle("Account settings").count()) > 0;
  log("  signed in with TEMP password:", loggedInWithTemp);

  // 8. RESTORE: change TEMP -> PASS so the smoke account is left untouched.
  // Reach /account via the top-bar email click (hash router — a bare
  // goto("/account") bounces to /dashboard via the catch-all redirect).
  log("STEP restore original password (TEMP -> PASS)");
  await page.getByTitle("Account settings").first().click();
  await page.waitForTimeout(900);
  await changePassword(TEMP, PASS);
  const ok2 = await page.getByText(/password has been updated/i).count();
  log("  restore success alert shown:", ok2 > 0);

  // 9. Final proof original works again
  log("STEP verify original password restored");
  await page.getByRole("button", { name: /Sign out/i }).click();
  await page.waitForTimeout(1500);
  await page.fill('input[type="email"]', EMAIL);
  await page.fill('input[type="password"]', PASS);
  await page.click('button[type="submit"]');
  await page.waitForTimeout(2500);
  const restoredOk = !/login/i.test(page.url()) && (await page.getByTitle("Account settings").count()) > 0;
  log("  signed in with ORIGINAL password:", restoredOk);

  log("CONSOLE_ERRORS:", JSON.stringify(consoleErrors.slice(0, 20), null, 2));
  log("DONE — original password restored:", restoredOk);
} catch (e) {
  log("SCRIPT_ERROR:", e.message);
  await shot("99-error");
} finally {
  await browser.close();
}
