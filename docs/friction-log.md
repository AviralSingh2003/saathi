# Friction log

Entries recorded while building Saathi. Format: task · steps · expected vs actual · severity · workaround ·
suggestion. Severity: High (blocked) / Medium (slowed) / Low (annoyance).

### 1. MCP Python SDK 2.x renames break most examples
- **Task:** stand up a Streamable HTTP MCP server.
- **Steps:** followed common examples using `from mcp.server.fastmcp import FastMCP` and `streamablehttp_client`.
- **Expected:** imports work. **Actual:** `ModuleNotFoundError`; 2.x renamed `FastMCP` → `MCPServer` and
  `streamablehttp_client` → `streamable_http_client` (which also dropped `headers=`/`httpx_client_factory`).
- **Severity:** Medium. **Workaround:** read the migration guide and introspected the installed package.
- **Suggestion:** a deprecation shim that raises a message pointing to the new names would save time.

### 2. Strands pins `mcp<2.2`, while MCP docs describe 2.3
- **Task:** use the newest MCP server options next to Strands in one environment.
- **Expected:** docs options available. **Actual:** pip resolves `mcp==2.1.1`; options documented for
  2.3 (`session_idle_timeout`, `max_sessions` on `streamable_http_app`) don't exist there.
- **Severity:** Low. **Workaround:** pinned `mcp==2.1.1` and avoided 2.3-only options.
- **Suggestion:** state the supported MCP SDK range in the Strands MCP docs.

### 3. Strands' MCP floor allows SDKs that can't negotiate 2025-11-25
- **Task:** guarantee protocol 2025-11-25 (a hackathon requirement).
- **Actual:** `strands-agents` allows `mcp>=1.23`, but 2025-11-25 first appears in mcp 1.24.
- **Severity:** Low. **Workaround:** pinned a newer SDK; a protocol test asserts the negotiated version.
- **Suggestion:** raise the floor to 1.24, or document it.

### 4. Tool exceptions are hidden unless they're `ToolError`
- **Task:** return helpful consent errors ("Not sent: the person has to approve…").
- **Expected:** exception message reaches the client. **Actual:** generic crash text; only `ToolError`
  messages pass through (by design, for safety).
- **Severity:** Medium (looked like a bug at first). **Workaround:** `SaathiError(ToolError)`.
- **Suggestion:** mention this prominently in the tools docs page; it's a good default that's easy to miss.

### 5. Custom Strands model: docs example has the wrong event shape
- **Task:** implement an offline `MockModel` that triggers tool calls.
- **Actual:** the docs show `contentBlockStart: {start: {name, toolUseId}}`; the agent only treats it as a
  tool call with the `toolUse` wrapper: `{start: {toolUse: {name, toolUseId}}}` (as in the repo's test fixture).
- **Severity:** Medium. **Workaround:** copied the event sequence from `tests/fixtures/mocked_model_provider.py`.
- **Suggestion:** fix the example, and ship a public mock model for tests.

### 6. Strands `MCPClient` doesn't advertise MCP Apps
- **Task:** render MCP Apps cards for tools called by the agent.
- **Actual:** the server's `client_supports_apps()` is false for the Strands session; the agent has no way to
  render UI anyway.
- **Severity:** Low. **Workaround:** the host relays `{tool, input, result, resourceUri}` to the browser, which
  reads the `ui://` resource with its own MCP session and renders it with `AppBridge`.
- **Suggestion:** document the recommended pattern for agent hosts that sit in front of a UI.

### 7. Agent Skills: filesystem skills look empty without an agent
- **Task:** verify the skill is loaded.
- **Actual:** `AgentSkills.get_available_skills()` returns `[]`; filesystem skills are only resolved per agent
  (`get_available_skills(agent)`). Also, skills only load when the model chooses to activate them.
- **Severity:** Low. **Workaround:** check with the agent; also put the guardrails in the system prompt so they
  always apply.
- **Suggestion:** log the loaded skill names at `init_agent`, and document an "always active" option.

### 8. MCP Apps card runtime is large when inlined
- **Task:** single-file `ui://` cards (no network inside the sandbox).
- **Actual:** bundling ext-apps' `App` produces ~600 KB minified per card.
- **Severity:** Low. **Workaround:** acceptable for local use.
- **Suggestion:** a lightweight view-side runtime without schema validation.

### 9. MCP Apps auto-resize misses a late render
- **Task:** card iframe sized to content.
- **Actual:** a card that re-rendered after its first measurement stayed clipped.
- **Severity:** Low. **Workaround:** call `app.sendSizeChanged()` after each render.

### 10. Alexa+ developer docs vs. partner-only tooling
- **Task:** connect the self-hosted MCP server to Alexa+.
- **Actual:** setup pages reference the MCP Toolkit / Web Simulator, which aren't available to participants
  (confirmed in the hackathon FAQ).
- **Severity:** Medium. **Workaround:** built a web-based simulated Alexa+ host that's a real MCP client.
- **Suggestion:** put the availability note on every add-on setup page.

### 11. Strands `tool_filters` hide tools from the model, not from callers
- **Task:** a host-only tool (`reflect_turn`) the agent's model must not see, called by the host after each turn.
- **Actual:** `MCPClient(tool_filters={"rejected": [...]})` removes the tool from `list_tools_sync()` (so the
  model never sees it), but `call_tool_sync()` still calls it. Useful here, but undocumented. A filter is not an
  access control.
- **Severity:** Low. **Workaround:** relied on it, with a test (`test_reflection_is_hidden_from_the_model`). Family
  tools are protected by a server-side token check, not by the filter.
- **Suggestion:** document that filters only shape the agent's tool list.

### 12. Refreshing an agent's system prompt every turn, alongside `AgentSkills`
- **Task:** put long-term memory ("What you know about Meera") into the system prompt before every turn.
- **Actual:** `Agent.system_prompt` has a setter, so it can be replaced between invocations. Whether the
  `AgentSkills` plugin's injected skill list survives that isn't documented. Reading the source showed the
  plugin re-injects it in a `BeforeInvocationEvent` hook (and removes its previous copy), so the two coexist.
- **Severity:** Low. **Workaround:** set `system_prompt` before `invoke_async`, covered by
  `test_what_saathi_knows_is_in_every_prompt`.
- **Suggestion:** document per-invocation system prompts (or a context-provider hook) as a supported pattern
  for memory-backed agents.

### 13. A hard time limit on a Bedrock call
- **Task:** an emergency check that must answer within 10 seconds or fall back to asking the person.
- **Actual:** boto3's default timeouts are 60 s connect / 60 s read, with retries, so a stalled call can run
  for minutes. `asyncio.wait_for` around `asyncio.to_thread` stops waiting but can't cancel the thread.
- **Severity:** Medium (safety-relevant). **Workaround:** both:
  - `botocore.config.Config(connect_timeout, read_timeout, retries={"max_attempts": 1})` on the client
  - `asyncio.wait_for` as the outer limit; the late result is ignored
- **Suggestion:** a documented "latency budget" option for `converse`, and a note in the Bedrock docs on
  timeouts for interactive and safety use.

### 15. The Free plan silently blocks SES and SMS
- **Task:** send family alerts from a new AWS account.
- **Actual:** Bedrock worked, but SES failed with `explicit deny in a service control policy`
  (`organizations::…`), and the SMS console said "not available on free tier". Neither the IAM console nor the
  SES console says the account plan is the cause. The organization isn't visible to the account, so the
  error reads like a misconfigured IAM policy.
- **Severity:** Medium. **Workaround:**
  - Gmail SMTP as an email fallback (SES, then SMTP, then outbox).
  - A `/phone` page showing every text and email Saathi sends.
- **Suggestion:** say "not available on the Free plan; upgrade to use SES" in the SES console and in the
  AccessDenied message.

### 14. SMS origination rules for alerts
- **Task:** text family members (US or India) from Amazon SNS.
- **Actual:** SNS `publish` is simple, but whether the text arrives depends on country-specific rules. The
  US needs a registered toll-free or 10DLC number; India needs DLT registration. These live in AWS End User
  Messaging docs rather than the SNS page that shows `publish`.
- **Severity:** Medium for a demo. **Workaround:** the sandbox with a verified number, email as a second
  channel, and offline `.sms.txt` files that show exactly what would be sent.
- **Suggestion:** an origination-requirements table on the SNS SMS quick-start page.
