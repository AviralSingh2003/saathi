// Elder view: the simulated Alexa+ device.
// - Talks to the host agent over a WebSocket (utterances in, spoken replies out).
// - Holds its own direct MCP session to the Saathi server, used to read ui:// cards
//   (resources/read) and render them as MCP Apps. Its raw JSON-RPC frames, plus the
//   host agent's tool calls, stream into the inspector.
import {
  FrameRecorder,
  attachTypedInput,
  connectMcp,
  createVoice,
  mountInspector,
  renderToolCard,
} from "./vendor/mcp-voice-sim.js";

const cfg = window.SAATHI_CONFIG;
const $ = (id) => document.getElementById(id);
const recorder = new FrameRecorder({ limit: 300 });
mountInspector($("inspector"), recorder);

// ---------- status ----------
let statusTimer;
function status(text, ms = 4000) {
  $("status").textContent = text;
  $("status").classList.add("show");
  clearTimeout(statusTimer);
  if (ms) statusTimer = setTimeout(() => $("status").classList.remove("show"), ms);
}

// ---------- direct MCP session (cards + inspector) ----------
let mcpPromise;
function mcp() {
  mcpPromise ??= connectMcp(cfg.mcpUrl, {
    recorder,
    clientInfo: { name: "saathi-elder-view", version: "0.1.0" },
  }).catch((err) => {
    mcpPromise = undefined;
    throw err;
  });
  return mcpPromise;
}
mcp().catch((err) => status(`Couldn't open MCP session: ${err.message}`, 0));

// ---------- speaker selection ----------
for (const person of cfg.people) {
  const option = document.createElement("option");
  option.value = person.person_id;
  option.textContent = person.name;
  $("speaker").append(option);
}
const currentPerson = () => $("speaker").value;
const nameOf = (personId) => cfg.people.find((p) => p.person_id === personId)?.name ?? personId;

// ---------- per-person conversation + cards ----------
// Each person has their own conversation log and card stack. Switching the
// speaker swaps which one is shown; nothing is lost or mixed between people.
const views = new Map(); // person_id -> { log, cards }

function viewFor(personId) {
  if (!views.has(personId)) {
    const log = document.createElement("ol");
    log.className = "log";
    log.dataset.person = personId;
    const empty = document.createElement("li");
    empty.className = "log-empty";
    empty.textContent = `No conversation with ${nameOf(personId)} yet.`;
    log.append(empty);

    const cards = document.createElement("div");
    cards.className = "card-stack";
    cards.dataset.person = personId;
    cards.innerHTML = '<p class="muted">Cards appear here.</p>';

    const active = personId === currentPerson();
    for (const el of [log, cards]) {
      el.hidden = !active;
      el.classList.toggle("active", active);
    }
    $("logs").append(log);
    $("cards").append(cards);
    views.set(personId, { log, cards });
  }
  return views.get(personId);
}

function showPerson(personId) {
  viewFor(personId);
  for (const [id, view] of views) {
    const active = id === personId;
    view.log.hidden = !active;
    view.cards.hidden = !active;
    view.log.classList.toggle("active", active);
    view.cards.classList.toggle("active", active);
  }
  views.get(personId).log.lastElementChild?.scrollIntoView({ block: "end" });
}

function addLine(personId, who, text, extra = "") {
  const { log } = viewFor(personId);
  log.querySelector(".log-empty")?.remove();
  const li = document.createElement("li");
  li.className = `line ${who} ${extra}`.trim();
  const label = document.createElement("span");
  label.className = "who";
  label.textContent = who === "saathi" ? "Saathi" : who === "memory" ? "Saathi's memory" : nameOf(personId);
  const body = document.createElement("span");
  body.className = "text";
  body.textContent = text;
  li.append(label, body);
  log.append(li);
  if (personId === currentPerson()) li.scrollIntoView({ behavior: "smooth", block: "end" });
}

showPerson(currentPerson());

// ---------- voice ----------
const voice = createVoice({
  lang: cfg.lang,
  onTranscript: (text) => sendUtterance(text),
  onState: (state, detail) => {
    $("talk").classList.toggle("listening", state === "listening");
    $("talk").setAttribute("aria-pressed", String(state === "listening"));
    $("talk-label").textContent = state === "listening" ? "Listening…" : "Tap to talk";
    if (state === "error" && detail !== "no-speech") status(`Microphone: ${detail}`);
  },
});
if (!voice.canListen) {
  $("talk").disabled = true;
  $("talk-label").textContent = "Voice needs Chrome or Edge — type below";
}
$("talk").addEventListener("click", () => (voice.listening ? voice.stop() : voice.listen()));
attachTypedInput($("typed"), $("typed-input"), (text) => sendUtterance(text));

async function speak(text) {
  await voice.speak(text);
  if ($("handsfree").checked && voice.canListen && text.trim().endsWith("?")) voice.listen();
}

// ---------- cards ----------
async function showCard(card) {
  try {
    const { client } = await mcp();
    const stack = viewFor(card.person_id ?? currentPerson()).cards;
    stack.querySelector(".muted")?.remove();
    const holder = document.createElement("div");
    holder.className = "card-holder";
    stack.prepend(holder);
    while (stack.children.length > 3) stack.lastElementChild.remove();
    await renderToolCard({
      container: holder,
      client,
      uri: card.uri,
      toolInput: card.input,
      toolResult: card.result,
      title: card.tool,
    });
  } catch (err) {
    status(`Card failed: ${err.message}`);
  }
}

// ---------- host agent bridge ----------
let ws;
function connectWs() {
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.addEventListener("open", () => ws.send(JSON.stringify({ type: "hello", person_id: $("speaker").value })));
  ws.addEventListener("message", (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "reply") {
      const personId = msg.person_id ?? currentPerson();
      addLine(personId, "saathi", msg.text, msg.escalated ? "escalated" : msg.proactive ? "proactive" : "");
      // Only speak to whoever is at the device now; a late reply for someone else is just filed.
      if (personId === currentPerson()) speak(msg.text);
    } else if (msg.type === "remembered") {
      // Be open about what Saathi keeps: show it, quietly, in that person's conversation.
      for (const fact of msg.facts) addLine(msg.person_id ?? currentPerson(), "memory", `Remembered: “${fact}”`);
    } else if (msg.type === "card") {
      showCard(msg);
    } else if (msg.type === "frame") {
      recorder.record(msg.direction, msg.message, "host agent");
    } else if (msg.type === "error") {
      status(msg.text, 8000);
    }
  });
  ws.addEventListener("close", () => {
    status("Reconnecting…", 2000);
    setTimeout(connectWs, 1500);
  });
}
connectWs();

function sendUtterance(text) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return status("Not connected yet");
  addLine(currentPerson(), "person", text);
  ws.send(JSON.stringify({ type: "utterance", text }));
}

$("speaker").addEventListener("change", () => {
  voice.stop();
  showPerson(currentPerson());
  ws?.send(JSON.stringify({ type: "hello", person_id: currentPerson() }));
  status(`Now speaking: ${nameOf(currentPerson())}`);
});

// ---------- simulated clock ----------
async function refreshClock() {
  try {
    const { now } = await (await fetch("/api/clock")).json();
    $("clock").textContent = now.slice(11, 16);
  } catch {
    /* server restarting */
  }
}
refreshClock();
setInterval(refreshClock, 5000);

async function setClock(body) {
  const res = await fetch("/api/clock", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json();
  if (data.now) $("clock").textContent = data.now.slice(11, 16);
  ws?.send(JSON.stringify({ type: "poll_now" }));
}

if (cfg.demoMode) {
  document.addEventListener("keydown", (event) => {
    if (event.target.tagName === "INPUT") return;
    if (event.key.toLowerCase() === "d") $("devbar").hidden = !$("devbar").hidden;
  });
  for (const button of document.querySelectorAll("[data-advance]")) {
    button.addEventListener("click", () => setClock({ advance_minutes: Number(button.dataset.advance) }));
  }
  $("set-time-btn").addEventListener("click", () => $("set-time").value && setClock({ set: $("set-time").value }));
  $("poll-now").addEventListener("click", () => ws?.send(JSON.stringify({ type: "poll_now" })));
}

status(cfg.mode === "mock" ? "Offline mode: deterministic router, real MCP calls" : "Live mode: Claude on Amazon Bedrock");
