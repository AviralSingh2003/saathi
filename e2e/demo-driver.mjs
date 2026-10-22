// Demo driver for the video: plays the Saathi demo scene by scene in a real browser window, so you
// only have to screen-record it. It starts a fresh Saathi (or attaches to one), opens the "stage"
// (the elder's device and Priya's phone side by side), types each line, waits for Saathi, and
// writes a transcript of what actually happened.
//
//   cd e2e
//   npm run demo                      # start a fresh Saathi with your .env (live or offline) and play
//   npm run demo -- --attach          # use a Saathi you already started with --no-browser
//   npm run demo -- --pace 1.5        # longer pauses between scenes (1 = default)
//   npm run demo -- --countdown 8     # seconds to start your screen recorder (default 5)
//
// Start recording the browser window during the countdown (e.g. Win+Alt+R, or OBS window capture).
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, openSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createInterface } from "node:readline/promises";
import { chromium } from "playwright-core";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const RUNS = join(ROOT, "e2e", "demo-runs");
mkdirSync(RUNS, { recursive: true });
const argv = process.argv.slice(2);
const flag = (name) => argv.includes(`--${name}`);
const option = (name, fallback) => {
  const i = argv.indexOf(`--${name}`);
  return i >= 0 && argv[i + 1] ? argv[i + 1] : fallback;
};
const PACE = Number(option("pace", "1")) || 1;
const COUNTDOWN = Number(option("countdown", "5"));
const BASE = process.env.SAATHI_UI || `http://127.0.0.1:${process.env.UI_PORT || 8080}`;
const TOKEN = "priya-demo";
const TYPE_DELAY = 45; // ms per key, so viewers can read along

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const pause = (ms) => sleep(ms * PACE);
const stamp = new Date().toISOString().replace(/[:T]/g, "-").slice(0, 16);
const log = (...a) => console.log(...a);

async function up() {
  try {
    return (await fetch(`${BASE}/config.js`)).ok;
  } catch {
    return false;
  }
}

// ---------- start Saathi ----------
let demo = null;
if (flag("attach")) {
  if (!(await up())) throw new Error(`No Saathi at ${BASE}. Start it with: python scripts/demo.py --no-browser`);
} else {
  if (await up()) {
    console.error(`Saathi is already running at ${BASE}. Stop it first (Ctrl+C in its window), or use --attach.`);
    process.exit(1);
  }
  const py = [join(ROOT, ".venv", "Scripts", "python.exe"), join(ROOT, ".venv", "bin", "python")].find(existsSync) || "python";
  const out = openSync(join(RUNS, `saathi-${stamp}.log`), "w");
  log("Starting a fresh Saathi (scripts/demo.py --no-browser)…");
  demo = spawn(py, [join(ROOT, "scripts", "demo.py"), "--no-browser"], { cwd: ROOT, env: process.env, stdio: ["ignore", out, out] });
  for (let i = 0; i < 90 && !(await up()); i++) await sleep(1000);
  if (!(await up())) throw new Error("Saathi didn't start; see the log in e2e/demo-runs/.");
}
const stop = () => {
  if (!demo) return;
  try {
    // demo.py starts the MCP server and the host as child processes: stop the whole tree.
    if (process.platform === "win32") spawnSync("taskkill", ["/pid", String(demo.pid), "/T", "/F"], { stdio: "ignore" });
    else demo.kill("SIGINT");
  } catch { /* already gone */ }
  demo = null;
};
process.on("SIGINT", () => { stop(); process.exit(130); });

// ---------- the stage ----------
const browser = await chromium.launch({
  channel: process.env.BROWSER || "chrome",
  headless: flag("headless"),
  args: ["--start-maximized", "--autoplay-policy=no-user-gesture-required"],
});
const context = await browser.newContext({ viewport: flag("headless") ? { width: 1920, height: 1080 } : null });
const page = await context.newPage();
await page.goto(`${BASE}/stage.html?token=${TOKEN}`);
const elder = async () => {
  for (let i = 0; i < 100; i++) {
    const f = page.frames().find((fr) => fr.url().includes("/elder.html"));
    if (f) return f;
    await sleep(100);
  }
  throw new Error("elder view didn't load");
};
let frame = await elder();
await frame.waitForSelector("#typed-input");
const mode = await frame.evaluate(() => (window.SAATHI_CONFIG || {}).mode);
const live = mode === "bedrock";
log(`Saathi is ${live ? "LIVE (Claude on Bedrock)" : "OFFLINE (deterministic router)"} at ${BASE}`);

// ---------- helpers ----------
const transcript = [];
let scene = "";
const record = (who, text) => {
  transcript.push({ scene, who, text });
  log(`  ${who.padEnd(14)} ${text}`);
};
const replyCount = () => frame.evaluate(() => document.querySelectorAll(".log.active .line.saathi").length);
const lastReply = () => frame.evaluate(() => {
  const all = document.querySelectorAll(".log.active .line.saathi .text");
  return all.length ? all[all.length - 1].textContent : "";
});
const speechDone = () => frame.waitForFunction(() => !window.speechSynthesis?.speaking, null, { timeout: 25000 }).catch(() => {});
const stage = (fn, ...args) => page.evaluate(([f, a]) => window.stage[f](...a), [fn, args]);
const caption = (text, warn = false) => stage("caption", text, warn);
const setClock = (hhmm) => page.evaluate((t) => fetch("/api/clock", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ set: t }),
}), hhmm);
const inbox = () => page.evaluate((t) => fetch(`/api/inbox?token=${t}`).then((r) => r.json()), TOKEN);
let seenIds = new Set((await inbox()).messages.map((m) => m.id));

async function waitReply(before, timeout = 60000) {
  await frame.waitForFunction((n) => document.querySelectorAll(".log.active .line.saathi").length > n, before, { timeout });
  const text = await lastReply();
  record("Saathi", text);
  await speechDone();
  return text;
}

async function say(who, text, { after = 1800 } = {}) {
  const before = await replyCount();
  const box = frame.locator("#typed-input");
  await box.click();
  await box.pressSequentially(text, { delay: TYPE_DELAY });
  record(who, text);
  await pause(400);
  await box.press("Enter");
  const reply = await waitReply(before);
  await pause(after);
  return reply;
}

async function waitForSaathiToSpeakFirst(fallbackTime, timeout = 30000) {
  const before = await replyCount();
  try {
    return await waitReply(before, timeout);
  } catch {
    await setClock(fallbackTime);
    return waitReply(before, timeout);
  }
}

async function phone(timeout = 30000, { quietIfNone = false } = {}) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    const fresh = (await inbox()).messages.filter((m) => !seenIds.has(m.id)).reverse();
    if (fresh.length) {
      await sleep(2500); // let the email copy land too, and the phone page refresh
      const all = (await inbox()).messages.filter((m) => !seenIds.has(m.id)).reverse();
      for (const m of all) {
        seenIds.add(m.id);
        record(m.type === "email" ? "Priya (email)" : "Priya (text)", m.type === "email" ? m.subject : m.body);
      }
      await pause(3500);
      return all;
    }
    await sleep(1000);
  }
  if (!quietIfNone) record("(note)", "nothing reached Priya's phone in time");
  return [];
}

const shot = (name) => (flag("shots") ? page.screenshot({ path: join(RUNS, `${stamp}-${name}.png`) }) : null);

async function speaker(id) {
  await frame.selectOption("#speaker", id);
  await pause(900);
}

// ---------- countdown ----------
log(`\nStart your screen recorder now. Scenes begin in ${COUNTDOWN} s…`);
await caption("Starting…");
for (let s = COUNTDOWN; s > 0; s--) { await caption(`Starting in ${s}…`); await sleep(1000); }
await caption("");
const started = Date.now();

// ---------- scene 1: Saathi speaks first, and remembers ----------
scene = "1. Morning check-in (09:00): Saathi speaks first and remembers";
log(`\n${scene}`);
await caption("Meera · 9:00 in the morning");
await waitForSaathiToSpeakFirst("09:00");
await pause(1200);
await say("Meera", "They won, and Arjun hit the winning shot!");
await shot("1-checkin");

// ---------- scene 2: a reminder, read back, shown as an MCP Apps card ----------
scene = "2. A reminder (MCP Apps card)";
log(`\n${scene}`);
const card = frame.locator(".card-stack.active iframe");
const cardsBefore = await card.count();
await say("Meera", "Remind me to take my BP tablet at 2", { after: 800 });
if ((await card.count()) === cardsBefore) await say("Meera", "Yes, please", { after: 800 });
await frame.waitForFunction((n) => document.querySelectorAll(".card-stack.active iframe").length > n, cardsBefore,
  { timeout: 20000 }).catch(() => record("(note)", "no reminder card appeared"));
await pause(2500);

// ---------- scene 3: Raj's quiet morning -> a gentle note to Priya ----------
scene = "3. Raj's quiet morning (09:30): engagement below his usual -> a note to Priya";
log(`\n${scene}`);
await speaker("raj");
await caption("Raj · 9:30 in the morning");
await setClock("09:30");
await waitForSaathiToSpeakFirst("09:30");
await pause(1000);
await say("Raj", "Fine.", { after: 1000 });
await say("Raj", "Not much.", { after: 1000 });
await say("Raj", "I watched the news.", { after: 500 });
await caption("Saathi notices Raj is quieter than his own usual");
await phone(40000);
await shot("3-quiet-morning");

// ---------- scene 4: Meera's words reach her daughter ----------
scene = "4. A message for Priya";
log(`\n${scene}`);
await speaker("meera");
await caption("Meera");
await say("Meera", "Tell Priya the roses are out", { after: 500 });
await phone(30000);

// ---------- scene 5: an emergency the keyword list can't catch ----------
scene = "5. Emergency: emergency services first, then Priya";
log(`\n${scene}`);
await speaker("raj");
await caption("Emergency dispatch is simulated in this prototype", true);
const emergencyLine = live
  ? "My words are coming out all wrong and my arm feels heavy"   // no keywords: the Haiku check has to catch it
  : "My chest feels tight and my left arm has gone numb";        // offline: the instant phrase list
await say("Raj", emergencyLine, { after: 500 });
await phone(30000);
await shot("5-emergency");

// ---------- scene 6: the family page, the protocol, the close ----------
scene = "6. Family page, MCP inspector, close";
log(`\n${scene}`);
await caption("Priya's family page: how they've seemed, alerts, updates");
await stage("right", `family.html?token=${TOKEN}`, "Priya's family page");
await pause(7000);
await caption("Live MCP traffic: tools/call · reflect_turn · emergency_check");
await frame.locator("#inspector-panel > summary").click();
await frame.evaluate(() => {
  // Show the inspector rows: give the panel room and bring it into view.
  const inspector = document.getElementById("inspector");
  if (inspector) inspector.style.maxHeight = "60vh";
  document.getElementById("inspector-panel").scrollIntoView({ behavior: "smooth", block: "end" });
});
await pause(4500);
await shot("6-family-inspector");
await caption("");
await stage("end", true);
await pause(5000);
await shot("7-end");

const seconds = Math.round((Date.now() - started) / 1000);
log(`\nDone in ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")} (before editing).`);

// ---------- transcript of this run ----------
let md = `# Demo run, ${new Date().toLocaleString()}\n\nMode: ${live ? "live (Claude on Amazon Bedrock)" : "offline (deterministic router)"}` +
  ` · recorded length ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}\n`;
let current = "";
for (const line of transcript) {
  if (line.scene !== current) {
    current = line.scene;
    md += `\n## ${current}\n\n`;
  }
  md += `- **${line.who}:** ${line.text}\n`;
}
const file = join(RUNS, `transcript-${stamp}.md`);
writeFileSync(file, md, "utf8");
log(`Transcript: ${file}`);

if (flag("headless") || flag("exit")) {
  await browser.close();
  stop();
} else {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  await rl.question("\nStop your recording, then press Enter to close the browser and Saathi… ");
  rl.close();
  await browser.close();
  stop();
}
