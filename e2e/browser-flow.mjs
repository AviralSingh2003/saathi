// End-to-end check in a real browser (installed Chrome or Edge, headless).
// Prerequisite: the demo is running (`python scripts/demo.py --no-browser`).
// This script re-seeds the demo data itself, then drives the whole storyline
// through the elder page and the family page, asserting along the way.
//
//   cd e2e && npm install && npm test            # Chrome
//   BROWSER=msedge npm test                       # Edge
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import { chromium } from "playwright-core";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const BASE = process.env.SAATHI_UI || "http://127.0.0.1:8080";
const SHOTS = join(ROOT, "e2e", "screenshots");
mkdirSync(SHOTS, { recursive: true });

// ---- fresh demo data (clock starts 08:59:50, so the 09:00 check-in arrives on its own)
const py = [join(ROOT, ".venv", "Scripts", "python.exe"), join(ROOT, ".venv", "bin", "python")].find(existsSync) || "python";
const seeded = spawnSync(py, [join(ROOT, "scripts", "seed.py")], { cwd: ROOT, encoding: "utf8" });
assert.equal(seeded.status, 0, `seed failed: ${seeded.stderr}`);

const browser = await chromium.launch({ channel: process.env.BROWSER || "chrome", headless: !process.env.HEADED });
const context = await browser.newContext({ viewport: { width: 1400, height: 950 } });
const problems = [];

async function open(path, label) {
  const page = await context.newPage();
  page.on("console", (m) => m.type() === "error" && problems.push(`[${label}] ${m.text()}`));
  page.on("pageerror", (e) => problems.push(`[${label}] ${e.message}`));
  await page.goto(BASE + path);
  return page;
}

const elder = await open("/", "elder");
const replies = () => elder.locator(".log.active .line.saathi .text").allTextContents();
async function nextReply(action) {
  const before = (await replies()).length;
  await action();
  await elder.waitForFunction((n) => document.querySelectorAll(".log.active .line.saathi").length > n, before, { timeout: 30000 });
  const all = await replies();
  return all[all.length - 1];
}
const say = (text) => nextReply(async () => {
  await elder.fill("#typed-input", text);
  await elder.press("#typed-input", "Enter");
});
const setClock = (hhmm) => nextReply(() => elder.evaluate((t) => fetch("/api/clock", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ set: t }),
}), hhmm));
const step = (label, text, expected) => {
  console.log(`  ${label.padEnd(34)} ${text}`);
  assert.match(text, expected);
};

console.log("Elder page");
await elder.waitForFunction(() => document.querySelectorAll("#inspector details").length >= 2, null, { timeout: 15000 });
step("direct MCP session", "initialize/tools-list frames visible", /./);
step("proactive 09:00 check-in", await nextReply(async () => {}), /^Good morning, Meera\. Last time you mentioned/);
step("answer the follow-up", await say("They won by 20 runs!"), /I'll remember that/);
step("ask for a reminder", await say("Remind me to take my BP tablet at 2"), /^Just to check: remind you to take your BP tablet at 2 in the afternoon\?/);
step("confirm", await say("yes"), /I'll remind you to take your BP tablet at 2 in the afternoon/);
const reminderCard = elder.frameLocator(".card-stack.active iframe").first();
await reminderCard.locator(".label").waitFor({ timeout: 15000 });
step("reminder card (MCP App)", (await reminderCard.locator(".card").innerText()).replace(/\s+/g, " "), /REMINDER SET take my BP tablet/i);
step("mention a plan", await say("My granddaughter Diya starts college next week."), /^Thank you for telling me, Meera\.$/);
const memoryLine = elder.locator(".log.active .line.memory .text");
await memoryLine.first().waitFor({ timeout: 15000 });
step("kept automatically (shown)", await memoryLine.first().innerText(), /^Remembered: “My granddaughter Diya starts college next week”$/);
await elder.waitForTimeout(600); // let the smooth scroll finish
await elder.screenshot({ path: join(SHOTS, "elder-memory.png") });
step("14:00 reminder fires", await setClock("14:00"), /^It's 2 in the afternoon\. Time to take your BP tablet/);
step("self-reported feeling", await say("I'm feeling pretty good"), /Thank you for telling me/);
step("done", await say("done"), /^Got it, I've noted that's done/);
step("message for Priya, sent at once", await say("Tell Priya the roses are out"), /^Done\. I've passed that on to Priya\.$/);
await elder.locator("#inspector-panel > summary").click();
await elder.screenshot({ path: join(SHOTS, "elder.png") });
const frames = await elder.locator("#inspector details > summary").allTextContents();
assert.ok(frames.some((f) => f.includes("[host agent] tools/call send_family_update")), "host tool call missing from inspector");
assert.ok(frames.some((f) => f.includes("[browser] resources/read")), "browser resources/read missing from inspector");

console.log("Family page");
const family = await open("/family.html?token=priya-demo", "family");
const dashboard = family.frameLocator("#dashboard iframe").first();
await dashboard.locator("h2").first().waitFor({ timeout: 15000 });
step("dashboard card (MCP App)", (await dashboard.locator(".card").innerText()).replace(/\s+/g, " ").slice(0, 120), /HOW MEERA HAS SEEMED/i);
assert.match(await dashboard.locator(".card").innerText(), /The roses are out/);
step("impressions, live on the dashboard", await dashboard.locator(".seemed .today").innerText(), /^Meera seemed cheerful today\.$/);
step("long-term insight", (await dashboard.locator(".insight").allTextContents()).join(" | "), /Has often seemed cheerful lately/);
assert.equal(await elder.locator(".log .line", { hasText: "seemed" }).count(), 0, "impressions leaked onto the elder screen");
await family.fill("#note-text", "Mum, I'll call Sunday at 6. Love you.");
await family.click("#note-form button");
await family.waitForFunction(() => document.querySelector("#note-status").textContent.startsWith("Saved"), null, { timeout: 15000 });
step("leave a note", await family.locator("#note-status").innerText(), /^Saved\./);
await family.waitForTimeout(1000); // let the card re-render and resize
await family.screenshot({ path: join(SHOTS, "family.png"), fullPage: true });

console.log("Elder page, evening");
step("18:00 check-in mentions the note", await setClock("18:00"), /^Good evening, Meera\. Priya left you a message/);
step("hear it", await say("yes"), /^Priya says: "Mum, I'll call Sunday at 6\. Love you\." Would you like to reply\?/);
step("reply, sent at once", await say("tell her I'll be ready at 6"), /^Sent\. Priya will get your reply\./);
step("what is shared (truthful)", await say("what do you share with Priya?"), /^I keep Priya up to date about your day/);
step("what is remembered", await say("what do you remember about me?"), /^Here's what I remember\. Your granddaughter Diya starts college next week\./);
assert.equal(await memoryLine.count(), 1, "something other than the plan was auto-saved");
await elder.screenshot({ path: join(SHOTS, "elder-end.png") });

console.log("Switching speaker");
const meeraLines = (await replies()).length;
await elder.selectOption("#speaker", "raj");
assert.equal((await replies()).length, 0, "Meera's conversation is still visible after switching to Raj");
assert.match(await elder.locator(".log.active .log-empty").innerText(), /No conversation with Raj yet/);
assert.equal(await elder.locator(".card-stack.active iframe").count(), 0, "Meera's cards are visible to Raj");
step("Raj: an emergency", await say("My chest feels tight and my left arm has gone numb"), /^I've called emergency services, Raj\. Help is on the way/);
assert.equal(await elder.locator(".log.active .line.escalated").count(), 1, "emergency reply not marked");
await elder.selectOption("#speaker", "meera");
assert.equal((await replies()).length, meeraLines, "Meera's conversation was not restored");
assert.ok(await elder.locator(".card-stack.active iframe").count() > 0, "Meera's cards were not restored");
console.log("  back to Meera                      her conversation and cards are restored");
await elder.selectOption("#speaker", "raj");
assert.equal((await replies()).length, 1, "Raj's conversation was not kept");
console.log("  back to Raj                        his conversation is kept separately");

await family.waitForTimeout(11000); // family page refreshes every 10 s
assert.match(await dashboard.locator(".card").innerText(), /I'll be ready at 6/);
console.log("  family sees the reply              ok");
const rajFamily = await open("/family.html?token=priya-demo-raj", "family raj");
const rajDashboard = rajFamily.frameLocator("#dashboard iframe").first();
await rajDashboard.locator(".alert.urgent").first().waitFor({ timeout: 15000 });
step("Raj's dashboard: urgent alert", await rajDashboard.locator(".alert.urgent").first().innerText(), /Emergency services have been contacted for Raj/);
await rajFamily.waitForTimeout(800);
await rajFamily.screenshot({ path: join(SHOTS, "family-emergency.png"), fullPage: true });

console.log("Priya's phone");
const phone = await open("/phone", "phone");
await phone.locator(".sms").first().waitFor({ timeout: 15000 });
const bubbles = await phone.locator(".sms .sms-body").allTextContents();
step("text: Meera's message", bubbles.find((t) => t.includes("The roses are out")) || "(missing)", /^Meera says: "The roses are out\."/);
step("text: urgent alert", await phone.locator(".sms.urgent .sms-body").last().innerText(), /^Emergency services have been contacted for Raj/);
await phone.waitForTimeout(500);
await phone.screenshot({ path: join(SHOTS, "phone-texts.png") });
assert.equal(await phone.locator("#tab-mail").isVisible(), false, "the phone shows emails by default (texts only expected)");
await phone.goto(BASE + "/inbox.html?token=priya-demo&mail=1");
await phone.locator(".sms").first().waitFor({ timeout: 15000 });
await phone.click("#tab-mail");
step("mail: update", (await phone.locator(".mail-row .mail-subject").allTextContents()).join(" | "), /An update about Meera/);
await phone.locator(".mail-row.urgent").first().click();
step("mail: urgent email opens", await phone.locator(".reader h3").innerText(), /^URGENT: emergency services contacted for Raj$/);
await phone.screenshot({ path: join(SHOTS, "phone-mail.png") });
await family.waitForTimeout(1000);
await family.screenshot({ path: join(SHOTS, "family-end.png"), fullPage: true });
// The card iframe must be tall enough for its content (no clipping).
const [frameHeight, contentHeight] = await Promise.all([
  family.locator("#dashboard iframe").evaluate((el) => el.getBoundingClientRect().height),
  dashboard.locator("body").evaluate((el) => el.getBoundingClientRect().height),
]);
assert.ok(frameHeight >= contentHeight - 2, `dashboard card clipped: frame ${frameHeight}px < content ${contentHeight}px`);

console.log("Family links without a token (demo mode)");
for (const path of ["/family", "/family.html"]) {
  const page = await open(path, `family ${path}`);
  await page.waitForFunction(() => location.search.includes("token="), null, { timeout: 10000 });
  await page.frameLocator("#dashboard iframe").first().locator("h2").first().waitFor({ timeout: 15000 });
  console.log(`  ${path.padEnd(34)} -> ${new URL(page.url()).pathname}${new URL(page.url()).search}  dashboard ok`);
  await page.close();
}

await browser.close();
if (problems.length) {
  console.error("\nBrowser console errors:\n" + problems.join("\n"));
  process.exit(1);
}
console.log(`\nAll browser checks passed. Screenshots: ${SHOTS}`);
