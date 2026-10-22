// Family view: a direct browser MCP client. It reads the dashboard card's ui://
// URI from tools/list (_meta.ui.resourceUri), renders it as an MCP App, and
// calls leave_family_note. Every JSON-RPC frame shows in the inspector.
import { FrameRecorder, connectMcp, fetchAppHtml, mountInspector, renderApp } from "./vendor/mcp-voice-sim.js";

const cfg = window.SAATHI_CONFIG;
const $ = (id) => document.getElementById(id);
// The access token comes from the link. In demo mode, fall back to the seeded demo
// token if the link lost its query string, and put it back in the address bar.
let token = new URLSearchParams(location.search).get("token");
if (!token && cfg.demoMode && cfg.demoFamilyToken) {
  token = cfg.demoFamilyToken;
  history.replaceState(null, "", `${location.pathname}?token=${encodeURIComponent(token)}`);
}
const recorder = new FrameRecorder({ limit: 200 });
mountInspector($("inspector"), recorder);

let session;
let card;
let cardHtml;

async function start() {
  if (!token) {
    $("dashboard").innerHTML = '<p class="muted">This link is missing its access token (?token=…).</p>';
    $("note-form").hidden = true;
    return;
  }
  session = await connectMcp(cfg.mcpUrl, { recorder, clientInfo: { name: "saathi-family-view", version: "0.1.0" } });
  const { tools } = await session.client.listTools();
  const dashboardTool = tools.find((t) => t.name === "get_family_dashboard");
  const uri = dashboardTool?._meta?.ui?.resourceUri;
  if (uri) cardHtml = await fetchAppHtml(session.client, uri);
  await refresh();
  setInterval(refresh, 10000);
}

async function refresh() {
  const result = await session.client.callTool({ name: "get_family_dashboard", arguments: { contact_token: token } });
  if (result.isError) {
    $("dashboard").innerHTML = `<p class="muted">${escapeHtml(result.content?.[0]?.text ?? "Error")}</p>`;
    return;
  }
  const data = result.structuredContent;
  $("note-title").textContent = `Leave a message for ${data.person_name}`;
  $("note-form").hidden = !data.can_leave_notes;
  if (!cardHtml) {
    $("dashboard").textContent = JSON.stringify(data, null, 2);
    return;
  }
  if (card) {
    await card.update(result);
  } else {
    $("dashboard").innerHTML = "";
    card = await renderApp({
      container: $("dashboard"),
      html: cardHtml,
      client: session.client,
      toolInput: { contact_token: "•••" },
      toolResult: result,
      title: "Family dashboard",
      minHeight: 240,
    });
  }
}

$("note-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("note-text").value.trim();
  if (!text) return;
  $("note-status").textContent = "Sending…";
  try {
    const out = await session.callTool("leave_family_note", { contact_token: token, text });
    $("note-text").value = "";
    $("note-status").textContent = `Saved. ${out.delivery}`;
    await refresh();
  } catch (err) {
    $("note-status").textContent = err.message.replace(/^Error executing tool \w+:\s*/, "");
  }
});

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

start().catch((err) => {
  $("dashboard").innerHTML = `<p class="muted">Couldn't connect: ${escapeHtml(err.message)}</p>`;
});
