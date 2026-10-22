# Product feedback

One section per tool, answering the Devpost questions: what it was used for · what worked · what needs work ·
onboarding (zero to hello world) · would you build again. The Amazon sections include what we saw running live.

## MCP specification & Python SDK (`mcp` 2.1.1)
- **Used for:** the Saathi server: 16 tools, Streamable HTTP at `/mcp`, protocol 2025-11-25.
- **Worked well:** `MCPServer` decorators with Pydantic return types give structured output for free;
  `TransportSecuritySettings` handles Origin/Host validation; built-in `Apps` extension.
- **Needs work:** 1.x → 2.x renames break most online examples; non-`ToolError` exceptions are hidden
  (good default, under-documented); 2.3 docs vs. the 2.1 line Strands allows.
- **Onboarding:** ~30 min to a working server once the 2.x names were known.
- **Again?** Yes — the security defaults and Apps support are strong.

## MCP TypeScript SDK (`@modelcontextprotocol/client` 2.3)
- **Used for:** the family page and elder page as direct browser MCP clients.
- **Worked well:** `Client` + `StreamableHTTPClientTransport` worked in the browser after bundling; session
  handling just worked once CORS exposed `Mcp-Session-Id`.
- **Needs work:** no first-party hook for observing raw frames (we wrapped `send`/`onmessage`).
- **Again?** Yes.

## MCP Apps extension (`@modelcontextprotocol/ext-apps` 2.0)
- **Used for:** reminder and family-dashboard cards.
- **Worked well:** `AppBridge` + `PostMessageTransport` on the host side; the Python SDK's `Apps()` stamps
  `_meta.ui.resourceUri` and the MIME type correctly.
- **Needs work:** large view-side bundle; auto-resize missed a late render; little guidance for agent hosts.
- **Again?** Yes.

## Agent Skills format
- **Used for:** `skills/saathi-companion` (persona, guardrails, check-in etiquette, emergency reference).
- **Worked well:** simple format; passed strict validation in Strands.
- **Needs work:** no way to mark a skill always-on; on-demand activation doesn't suit guardrails.
- **Again?** Yes.

## Strands Agents SDK (1.60)
- **Used for:** the host agent — `MCPClient` with tool filters, `BedrockModel`, `AgentSkills`, hooks, and a
  custom `Model` for offline mode.
- **Worked well:** `MCPClient(url=...)` was one line; hooks made `person_id` injection and inspector frames easy.
- **Needs work:**
  - The custom-model docs example has the wrong event shape, and there's no public mock model.
  - **Live, replies came back empty.** Claude often speaks *before* a tool call ("That's wonderful! Let me
    remember that.") and then ends with an empty message. `str(AgentResult)` only returns the last message's
    text, so the reply was blank. We now collect the assistant text from the whole turn. A "spoken reply"
    accessor would help.
  - **Blocking a tool call:** `BeforeToolCallEvent.cancel_tool` worked well for this (we use it to stop the
    model logging a feeling on a scheduled check-in), but it's hard to find in the docs.
- **Onboarding:** under an hour to an agent calling MCP tools.
- **Again?** Yes.

## Amazon Bedrock
- **Used for:**
  - conversation (Claude via Strands `BedrockModel`)
  - a time-limited Haiku emergency check on every message
  - background reflection (memory, impressions, concerns)
  - update phrasing (all `converse`)
- **Worked well:**
  - **Speed:** Claude Haiku 4.5 through the `us.` inference profile answered small `converse` calls in about
    1.7–2.3 s from India to us-east-1, comfortably inside the emergency check's 10-second limit. Full
    conversation turns felt natural.
  - **Testing:** `converse` with a JSON-only system prompt is simple to stub and test.
  - **Time limits:** botocore's `connect_timeout` / `read_timeout` and a single retry give a dependable upper
    bound for the emergency check.
  - **Writing:** the reflection's impressions read well and naturally ("seemed to gently correct Saathi
    about the timing…").
- **Needs work:**
  - **Model availability is unclear.** On a new account, Claude Haiku 5.5 failed in the playground with
    `AccessDeniedException: … not available for this account`, with no way forward except "contact AWS Sales".
    Haiku 4.5 worked straight away.
  - **Inference profile IDs are easy to get wrong:** the bare `anthropic.…` ID is rejected, and a regional
    prefix like `au.` silently fails outside its region.
  - **Tone needed prompting.** At first Claude answered a joke with "It's not funny at all" and asked "What's
    funny?" about a short reply. A short example in the system prompt fixed it. When the example matched the
    conversation exactly, Claude copied it word for word, so the example has to be on a different topic.
- **Throttling:** none seen at demo volumes.
- **Onboarding:** after the account setup, minutes: the use-case form, then the inference profile ID.
- **Again?** Yes.

## Amazon SES (v2)
- **Used for:** updates, replies, concern notes, evening digests, emergency alerts.
- **What happened live:** our account is on AWS's **Free plan**, and SES failed with `AccessDeniedException …
  explicit deny in a service control policy` from an organization we can't see. It reads like a broken IAM
  policy; nothing in the SES or IAM console says the account plan is the cause. We added an SMTP fallback
  (Gmail) so the demo could send real email.
- **Needs work:** say "SES isn't available on the Free plan; upgrade to use it" in the console and in the error.
- **Again?** Yes, on a paid account. The API itself is simple.

## Amazon SNS (SMS)
- **Used for:** texts to family: emergency alerts (urgent, ignoring quiet settings), concern notes,
  updates, replies and the evening digest. Sent with `publish(PhoneNumber=…)`, `SMSType=Transactional`.
- **Worked well (in code and tests):** direct-to-number publishing needs no topic or subscription; easy to stub.
- **What happened live:**
  - On the Free plan the SMS console said "not available on free tier", so texts couldn't be sent. The demo
    shows them on a phone-style page instead, exactly as they would be sent.
  - The "Sandbox destination phone numbers" section our setup steps described is no longer in the SNS
    console; SMS setup has moved to AWS End User Messaging SMS.
- **Needs work:**
  - Origination requirements differ sharply by country: US toll-free or 10DLC registration, India DLT.
  - Those requirements are spread across SNS and AWS End User Messaging pages.
  - A returned MessageId only means SNS accepted the text. Carrier or origination failures show up later,
    in delivery statistics.
- **Again?** Yes for alerts, on a paid account with an origination identity.

## Gmail SMTP (fallback)
- **Used for:** real email in the demo while SES was blocked.
- **Worked well:** with an App password, `smtplib` + STARTTLS on port 587 worked first time.
- **Needs work:** App passwords only appear after 2-Step Verification is on. On one new account the page just
  said "The setting you are looking for is not available for your account".

## Web Speech API
- **Used for:** speech in/out on the elder page.
- **Worked well:** zero setup in Chrome/Edge.
- **Needs work:** recognition unavailable in Firefox; Chrome sends audio to Google (stated in the README).

## Alexa+ developer documentation
- **Needs work:** partner-only tooling referenced from setup pages without an availability note.
