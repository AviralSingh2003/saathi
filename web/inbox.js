// "Their phone": every email and text Saathi sent this family member, from the outbox copies.
// Polls /api/inbox; newest first; urgent messages in red.

const cfg = window.SAATHI_CONFIG || {};
const params = new URLSearchParams(location.search);
let token = params.get("token") || cfg.demoFamilyToken || "";
if (!params.get("token") && token) history.replaceState(null, "", `?token=${encodeURIComponent(token)}${location.search.includes("mail=1") ? "&mail=1" : ""}`);
// Texts only by default (emails are shown in a real mail app); add ?mail=1 for the Mail tab too.
const SHOW_MAIL = params.get("mail") === "1";
if (!SHOW_MAIL) {
  document.getElementById("tab-mail").hidden = true;
  document.getElementById("mail").remove();
}

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const timeOf = (iso) => (iso || "").slice(11, 16);
const dayOf = (iso) => (iso ? new Date(iso).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }) : "");

let seen = null;           // ids already shown (null until the first load)
let unread = { text: 0, email: 0 };
let current = "texts";

$("back-link").href = `family.html?token=${encodeURIComponent(token)}`;

function show(tab) {
  current = tab;
  for (const name of SHOW_MAIL ? ["texts", "mail"] : ["texts"]) {
    $(name).classList.toggle("active", name === tab);
    $(`tab-${name}`).classList.toggle("active", name === tab);
    $(`tab-${name}`).setAttribute("aria-selected", String(name === tab));
  }
  $("reader").hidden = true;
  unread[tab === "texts" ? "text" : "email"] = 0;
  badges();
}

function badges() {
  for (const [kind, id] of [["text", "badge-texts"], ["email", "badge-mail"]]) {
    $(id).hidden = !unread[kind];
    $(id).textContent = unread[kind] || "";
  }
}

function chime() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = 880;
    gain.gain.setValueAtTime(0.08, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.4);
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.4);
  } catch { /* sound is optional */ }
}

function renderTexts(items) {
  if (!items.length) return `<p class="phone-empty">No messages yet.</p>`;
  return [...items].reverse().map((m) => `
    <div class="sms ${m.urgent ? "urgent" : ""}" data-id="${esc(m.id)}">
      <div class="sms-from">Saathi${m.urgent ? " · URGENT" : ""}</div>
      <div class="sms-body">${esc(m.body.replace(/^(?:URGENT from Saathi: |Saathi: |Saathi, )/, ""))}</div>
      <div class="sms-meta">${esc(dayOf(m.ts))} ${esc(timeOf(m.ts))}${m.channel === "sns" ? " · delivered" : ""}</div>
    </div>`).join("");
}

function renderMail(items) {
  if (!items.length) return `<p class="phone-empty">No email yet.</p>`;
  return items.map((m) => `
    <button class="mail-row ${m.urgent ? "urgent" : ""}" data-id="${esc(m.id)}">
      <span class="mail-top"><b>Saathi</b><span>${esc(timeOf(m.ts))}</span></span>
      <span class="mail-subject">${esc(m.subject)}</span>
      <span class="mail-preview">${esc(m.body.replace(/^Hi [^,]+,\s*/, "").slice(0, 90))}</span>
      ${m.channel === "smtp" || m.channel === "ses" ? `<span class="mail-sent">sent by email ✓</span>` : ""}
    </button>`).join("");
}

function openMail(m) {
  $("reader").innerHTML = `
    <button class="reader-back" id="reader-back">‹ Inbox</button>
    <h3 class="${m.urgent ? "urgent-text" : ""}">${esc(m.subject)}</h3>
    <div class="sms-meta">From Saathi · ${esc(dayOf(m.ts))} ${esc(timeOf(m.ts))}</div>
    <pre class="reader-body">${esc(m.body)}</pre>`;
  $("mail").classList.remove("active");
  $("reader").hidden = false;
  $("reader-back").addEventListener("click", () => show("mail"));
}

async function load() {
  let data;
  try {
    const r = await fetch(`/api/inbox?token=${encodeURIComponent(token)}`);
    data = await r.json();
    if (!r.ok) throw new Error(data.error || r.statusText);
  } catch (err) {
    $("texts").innerHTML = `<p class="phone-empty">${esc(err.message || err)}</p>`;
    return;
  }
  $("who").textContent = data.contact_name;
  if (!SHOW_MAIL) data.messages = data.messages.filter((m) => m.type === "text");
  const texts = data.messages.filter((m) => m.type === "text");
  const mail = data.messages.filter((m) => m.type === "email");
  const live = data.live || {};
  $("delivery-label").textContent = (SHOW_MAIL
    ? `Email: ${live.email ? `sent for real (${live.email.toUpperCase()})` : "demo copy only"} · ` : "") +
    `Texts: ${live.text ? "sent for real (SNS)" : "demo copy only"}`;
  $("phone-note").textContent = live.text
    ? ""
    : "Texts aren't sent from this setup; this is exactly what they would receive.";

  const fresh = seen ? data.messages.filter((m) => !seen.has(m.id)) : [];
  seen = new Set(data.messages.map((m) => m.id));
  for (const m of fresh) {
    if ((m.type === "text" && current !== "texts") || (m.type === "email" && current !== "mail")) unread[m.type] += 1;
  }
  if (fresh.length) chime();
  badges();

  const atBottom = $("texts").scrollHeight - $("texts").scrollTop - $("texts").clientHeight < 40;
  $("texts").innerHTML = renderTexts(texts);
  if (atBottom || fresh.some((m) => m.type === "text")) $("texts").scrollTop = $("texts").scrollHeight;
  if (SHOW_MAIL) {
    $("mail").innerHTML = renderMail(mail);
    for (const row of $("mail").querySelectorAll(".mail-row")) {
      row.addEventListener("click", () => openMail(mail.find((m) => m.id === row.dataset.id)));
    }
  }
}

async function clock() {
  // The demo clock, so the phone's time matches the message times.
  try {
    const { now } = await (await fetch("/api/clock")).json();
    $("phone-time").textContent = timeOf(now);
  } catch {
    $("phone-time").textContent = new Date().toTimeString().slice(0, 5);
  }
}

$("tab-texts").addEventListener("click", () => show("texts"));
if (SHOW_MAIL) $("tab-mail").addEventListener("click", () => show("mail"));
clock();
setInterval(clock, 5000);
load();
setInterval(load, 5000);
