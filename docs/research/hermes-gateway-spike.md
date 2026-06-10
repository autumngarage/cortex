# Spike: Hermes Agent as Slack/MCP gateway substrate for Cortex

- **Issue:** [cortex#456](https://github.com/autumngarage/cortex/issues/456)
- **Feeds:** [#457](https://github.com/autumngarage/cortex/issues/457) (build-vs-borrow boundary decision), alongside [#459](https://github.com/autumngarage/cortex/issues/459) (session-store/retention assessment); gates [#458](https://github.com/autumngarage/cortex/issues/458) (MCP/Slack prototype); consumed at Stage 3 entry ([#455](https://github.com/autumngarage/cortex/issues/455))
- **Date:** 2026-06-10
- **Author:** Claude (research agent), per the #456 assignment
- **Scope:** gateway concerns only — Socket Mode transport, event handling, MCP plumbing, deploy/runtime footprint, maintenance risk, and the core-ownership boundary. No prototype, no code.
- **Subject pinned at:** [`NousResearch/hermes-agent@a72bb03`](https://github.com/NousResearch/hermes-agent/commit/a72bb03757c0c925c686f9774eefc8dc5a77b329) (main as of 2026-06-10). Source-file citations below link `blob/main` paths; line-level claims were verified at this commit.

---

## The default, stated up front

Per `cortex_master_plan.md` (quoted in #456): **the default Slack gateway for the Stage 3 ledger console is the official Slack SDK/Bolt.** Hermes must clear a real evidence bar to displace it, and in every outcome Hermes never owns memory, sessions, prompts, search, confidence, or the decision graph — any leakage of those into Hermes is a named disqualifier.

**Verdict of this spike: Hermes does not clear the bar for the gateway role, and two boundary items (sessions, prompts) leak structurally in every supported Hermes deployment mode — both are named disqualifiers below.** The strongest argument for Hermes turns out to be self-defeating: its Slack transport *is* slack-bolt underneath, so adopting Hermes buys Bolt plus an agent runtime Cortex must then fence off. The genuinely good parts of Hermes' Slack layer — a socket watchdog and a TTL dedup cache — are ~160 lines of copyable, MIT-licensed pattern, not a moat. Recommendation: **Bolt direct, borrow the two resilience patterns with attribution.**

---

## 1. Subject identification

The issue describes "Hermes Agent — an MIT-licensed agent framework with a Slack Socket Mode gateway and MCP surfaces."

**Match: [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent)** ("The agent that grows with you"). All traits verified:

- MIT license, Python, created 2025-07-22, ~189,480 stars / ~32,743 forks as of 2026-06-10 (GitHub API, `repos/NousResearch/hermes-agent`).
- Slack Socket Mode gateway: [`gateway/platforms/slack.py`](https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/slack.py) — "Slack bot adapter using Socket Mode."
- MCP surfaces: built-in MCP client (since v0.2.0) and MCP server mode (since v0.6.0) per the project's [MCP docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp) and [`mcp_serve.py`](https://github.com/NousResearch/hermes-agent/blob/main/mcp_serve.py).

Ambiguity check: GitHub repo search for "hermes agent" returns this repo first by a wide margin; every other hit (`farion1231/cc-switch`, `nesquena/hermes-webui`, `fathah/hermes-desktop`, `0xNyk/awesome-hermes-agent`, etc.) is an ecosystem companion that names Hermes Agent in its description, not an agent framework itself. No other candidate has a Slack Socket Mode gateway plus MCP surfaces under an MIT license. Identification is unambiguous.

**Evidence base.** Direct source reading at the pinned commit (`gateway/platforms/slack.py`, 3,638 lines; `gateway/platforms/base.py`, 4,853 lines; `gateway/platforms/helpers.py`; `gateway/run.py`, 16,018 lines; `mcp_serve.py`; `gateway/memory_monitor.py`; `pyproject.toml`), the project's own docs site (project-authored — treated as claims about intent, cross-checked against source where load-bearing), Slack's official platform docs, the GitHub API for maintenance metrics, and Docker Hub for image sizes. Limitations are flagged inline as **unverified** where they exist.

---

## 2. Socket Mode transport handling

### What Hermes actually is at the transport layer

Hermes' Slack adapter is built **on the official Slack SDK**. The module docstring opens: *"Uses slack-bolt (Python) with Socket Mode"*, and the imports are `slack_bolt.async_app.AsyncApp`, `slack_bolt.adapter.socket_mode.async_handler.AsyncSocketModeHandler`, and `slack_sdk.web.async_client.AsyncWebClient` ([slack.py lines 1–30](https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/slack.py)). There is no independent Socket Mode implementation in Hermes. **Any transport-level comparison of "Hermes vs Bolt" is therefore really "Bolt-plus-wrappers vs Bolt."**

### What Bolt gives both sides

Slack's Socket Mode opens a WebSocket via `apps.connections.open`; the server sends `hello` with an `approximate_connection_time`, issues `disconnect` messages with reasons (`link_disabled`, `refresh_requested`) and a ~10-second warning before scheduled refreshes; apps must ack each payload by echoing its `envelope_id`; up to 10 simultaneous connections are allowed and payloads may arrive on any of them ([Slack: Using Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode/)). Delivery is effectively **at-least-once**: the Events API retries undelivered/unacked events up to 3 times (immediately, 1 min, 5 min) with retry metadata attached ([Slack: Events API retries](https://docs.slack.dev/apis/events-api/#retries)). Bolt's `SocketModeHandler` handles connect/refresh/ack mechanics.

### What Hermes adds on top (the interesting part)

1. **A socket watchdog.** A 15-second polling loop (`_socket_watchdog_loop`) checks that the Socket Mode asyncio task is alive and probes the client's `is_connected` state; on a dead task or disconnected transport it tears down and restarts the handler under a reconnect lock, with done-callbacks that also restart the watchdog itself if *it* dies ([slack.py, `_socket_watchdog_loop` / `_restart_socket_mode` / `_on_socket_mode_task_done`](https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/slack.py)). This is a mitigation for a real, documented weakness in the official SDK: silent Socket Mode disconnects where the client believes it is still connected — see [python-slack-sdk#1379](https://github.com/slackapi/python-slack-sdk/issues/1379) (stale `is_connected()`), [#1110](https://github.com/slackapi/python-slack-sdk/issues/1110), [#1280](https://github.com/slackapi/python-slack-sdk/issues/1280), and [#1462](https://github.com/slackapi/python-slack-sdk/issues/1462) (no reconnect after DNS outage).
2. **Event deduplication.** A TTL cache (`MessageDeduplicator`, default 2,000 entries / 300 s TTL, [gateway/platforms/helpers.py](https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/helpers.py)) suppresses redelivered events. The adapter's own comment states the reason: *"prevents duplicate bot responses when Socket Mode reconnects redeliver events"*, and it also de-dupes the `message`/`app_mention` double-fire for the same `ts`. Bolt does **not** deduplicate for you; at-least-once delivery makes this the consumer's job on either stack.
3. **Fast-ack via background processing.** Bolt handlers return quickly because the base adapter spawns message processing as a background task (`asyncio.create_task(self._process_message_background(...))`, [base.py line ~3671](https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/base.py)), keeping acks inside Slack's 3-second window regardless of LLM latency.
4. **Backpressure at the session level, not the transport level.** A two-level guard serializes work per conversation: if an agent run is active for a session key, new messages are queued in `_pending_messages` and an interrupt event is set; queued messages are drained into fresh background tasks afterward ([Hermes gateway-internals doc, "Two-Level Message Guard"](https://hermes-agent.nousresearch.com/docs/developer-guide/gateway-internals); implementation in `base.py`). There is no transport-level backpressure in either stack — Slack's WebSocket has no consumer-driven flow control; both stacks must absorb bursts in process memory.

### Assessment

Transport-wise, Hermes is evidence that **Bolt alone is not quite enough for an always-on production bot** — you need a liveness watchdog and an event dedup cache. It is *not* evidence that Hermes is needed: both patterns are small, self-contained, MIT-licensed, and directly portable into a Bolt-based Cortex gateway (~120 lines for the watchdog, ~40 for the dedup cache). These should be named requirements for the Stage 3 gateway regardless of the #457 outcome.

---

## 3. Event handling model

**Bolt:** a thin listener registry — `@app.event("message")`, `@app.command(...)`, `@app.action(...)` — where your code receives raw Slack payloads and decides everything. No opinions about sessions, threading semantics, or who replies.

**Hermes:** the same Bolt decorators, registered internally ([slack.py lines ~861–950](https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/slack.py): `message`, `app_mention`, `file_shared`/`file_created`/`file_change`, `assistant_thread_started`/`assistant_thread_context_changed`, a single regex-dispatched `command` matcher, and approval-button actions), normalized into a platform-agnostic `MessageEvent` and pushed through `BasePlatformAdapter`. The adapter exposes `set_message_handler(handler: MessageEvent -> Optional[str])` ([base.py ~line 2179](https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/base.py)) — superficially a clean seam — but in practice the pipeline is opinionated and agent-shaped:

- **Session keying is computed inside the adapter** (`agent:main:{platform}:{chat_type}:{chat_id}` via `build_session_key()` from `gateway/session.py`; Slack threads become per-thread sessions; the docs warn "Never construct session keys manually") ([gateway-internals](https://hermes-agent.nousresearch.com/docs/developer-guide/gateway-internals)).
- **The adapter writes to the session store directly** — e.g. `_seed_assistant_thread_session()` calls `session_store.get_or_create_session(...)` to pin user scoping for Slack AI-assistant threads, and `gateway/run.py` injects the store into every adapter via `adapter.set_session_store(self.session_store)` ([slack.py ~line 2074](https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/slack.py); [run.py](https://github.com/NousResearch/hermes-agent/blob/main/gateway/run.py)).
- **The gateway runner creates a Hermes `AIAgent` per message** with the session ID; memory-provider lifecycle hooks fire on session start/end ([gateway-internals, "Message Flow" and "Memory Provider Integration"](https://hermes-agent.nousresearch.com/docs/developer-guide/gateway-internals)).
- Gateway hooks (`gateway:startup`, `session:start/end/reset`, `agent:start/step/end`, `command:*`) are **observers**, not replacements for the agent loop ([gateway-internals, "Hooks"](https://hermes-agent.nousresearch.com/docs/developer-guide/gateway-internals)).

Mention-gating in channels, thread-context fetching, multi-workspace token routing, approval buttons, and slash-command ephemeral routing all come for free with Hermes and would be hand-built on Bolt. For the ledger console's scoped surface (`@cortex what did we decide about X?` + confirm/reject/merge/supersede actions), the hand-built equivalent is small: a mention listener, a thread-aware reply helper, and a handful of `block_actions` handlers.

**Assessment:** Hermes' event model is richer but inseparable from its agent runtime. The `set_message_handler` seam is not a supported standalone product — it lives inside the `hermes-agent` package, imports the session store, config system, and `hermes_constants` paths, and there is no documented way to run the gateway as a "dumb relay" that forwards events to an external brain without the Hermes `AIAgent` in the loop (see §6). Using only the adapter layer would mean vendoring a slice of a fast-moving 16k-line-gateway codebase — strictly worse than writing ~300 lines against Bolt.

---

## 4. MCP plumbing

What Hermes actually exposes ([MCP docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp); [`mcp_serve.py`](https://github.com/NousResearch/hermes-agent/blob/main/mcp_serve.py)):

- **MCP client (since v0.2.0):** connects to external MCP servers over stdio (`command`/`args`) or HTTP (`url`/`headers`), with OAuth 2.1 (`auth: oauth`), mTLS client certs, per-server `include`/`exclude` tool filters, and tools registered into the *Hermes agent's* tool registry as `mcp_<server>_<tool>`. This is how #458's "Cortex MCP tools exposed through Hermes" would work: Cortex runs an MCP server; Hermes' LLM loop discovers and calls Cortex tools mid-conversation.
- **MCP server (since v0.6.0):** `hermes mcp serve` — **stdio-only** (the docs state "no HTTP variant currently available") — exposing 10 tools (`conversations_list`, `conversation_get`, `messages_read`, `attachments_fetch`, `events_poll`, `events_wait`, `messages_send`, `channels_list`, `permissions_list_open`, `permissions_respond`). Mechanically it is a **polling bridge over Hermes' own SQLite session database** (~200 ms poll; `EventBridge` "polls SessionDB for new messages" — [mcp_serve.py](https://github.com/NousResearch/hermes-agent/blob/main/mcp_serve.py)); text-only sends, no media.

Two implications for Cortex:

1. **The client direction is the leak pathway, not a neutral pipe.** If Cortex tools are mounted into Hermes' MCP client, the entity deciding *when* to search the ledger, *what* to ask Cortex, and *how* to phrase the answer is Hermes' agent loop and its prompt assembly — i.e., prompts and the conversational decision-making move into Hermes (§6, items 3 and 6).
2. **The server direction doesn't fit the hosted topology.** A stdio-only MCP server requires the consumer to be co-located with the Hermes process and still only offers a read/send window onto Hermes' session DB — Hermes remains the system of record for conversation state, and the ~200 ms DB-polling bridge adds latency and a second store between Slack and Cortex.

**Bolt has no MCP surface at all** — which is the correct shape here: for the Stage 3 console, Slack events should hit Cortex's own service directly, and any MCP surface Cortex offers (the deferred MCP supply loop, per `.cortex/state.md`) should be Cortex-owned. Hermes' MCP plumbing solves a problem Cortex doesn't have (giving a *general* agent tool access) at the cost of inserting a general agent into the path.

---

## 5. Deploy/runtime footprint (Railway lens)

| Concern | Hermes gateway | Bolt-direct gateway |
|---|---|---|
| Process model | One long-running asyncio gateway process hosting the full agent runtime (provider adapters, tool registry "70+ tools", terminal/browser backends, memory manager, cron scheduler) ([architecture doc](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture)) | One small worker process: websocket + event handlers + HTTP calls to Cortex core |
| Image size | Official image ~**1.02–1.06 GB compressed** ([Docker Hub tags](https://hub.docker.com/r/nousresearch/hermes-agent/tags), `latest`/`v2026.6.5`, checked 2026-06-10) | `python:3.12-slim` + `slack-bolt`+`aiohttp`; tens of MB compressed |
| State | **Stateful by design**: persistent volume for `~/.hermes` (config, credentials, sessions SQLite, memories, skills); "Never run two Hermes gateway containers against the same data directory" ([Docker doc](https://hermes-agent.nousresearch.com/docs/user-guide/docker)) — single-writer constraint | Stateless; conversation state lives in Cortex's Postgres (already provisioned in Stage 1) |
| Supervision | s6-overlay supervision tree inside the container; gateway auto-restarts within the container ([Docker doc](https://hermes-agent.nousresearch.com/docs/user-guide/docker)) | Railway restart policy + the §2 watchdog; no in-container supervisor needed |
| Memory behavior | Ships a dedicated RSS-leak monitor (`gateway/memory_monitor.py`: logs an RSS time series because leaks "you only see... by watching RSS climb over hours") — evidence the project itself treats long-run RSS growth as a live concern. Steady-state RAM: **unverified** (no published figure; would need a measurement; the full agent stack makes several hundred MB plausible) | Minimal Bolt Socket Mode apps run in the tens-of-MB range (**unverified** precise figure; trivially measurable in #458) |
| Railway cost shape | RAM $10/GB/mo, CPU $20/vCPU/mo, volume $0.15/GB/mo ([Railway pricing](https://railway.com/pricing)); a ~1 GB-RSS stateful service with a volume costs roughly an order of magnitude more per month than a ~100 MB stateless worker | Marginal addition to the existing Stage 1 worker footprint |
| Security surface | The gateway box holds Slack tokens **and** a general agent with terminal/browser/file tools and lazy dependency installation (`tools/lazy_deps.py` installs `slack-bolt` at runtime if missing — [slack.py `check_slack_requirements`](https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/slack.py)); also requires an LLM provider credential just to run | Slack tokens + one outbound HTTPS dependency (Cortex API); no LLM credential in the gateway |

One genuine positive worth recording: Hermes pins every direct dependency exactly (`==X.Y.Z`, no ranges) and documents why — the May 2026 "Mini Shai-Hulud" PyPI worm hitting `mistralai` ([pyproject.toml comments](https://github.com/NousResearch/hermes-agent/blob/main/pyproject.toml)). Good hygiene on their side; also a reminder that the dependency tree Cortex would inherit is large enough to have been in the blast radius.

---

## 6. The boundary section: can Hermes be used without owning each core concern?

Frame: Hermes' only supported deployment modes all run its own agent core. The entry points (gateway, ACP, TUI JSON-RPC, OpenAI-compatible API server) "all drive the same `AIAgent` core" ([programmatic-integration doc](https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration)); the gateway "creates an `AIAgent` per message" ([gateway-internals](https://hermes-agent.nousresearch.com/docs/developer-guide/gateway-internals)). **No documented mode runs the Slack adapter as a pure relay to an external brain.** (Unverified: whether some undocumented config combination can suppress the built-in agent's responses while keeping the message-to-DB bridge alive; verifying that would require the #458 prototype, and even then sessions/prompts findings below still hold.)

Per-item verdicts:

| # | Concern | Can Hermes be used without owning it? | Evidence |
|---|---|---|---|
| 1 | **Memory** | **Yes, deniable by config — but default-on.** | `memory.memory_enabled: false` turns the MEMORY.md/USER.md system off entirely ([memory doc, "Configuration"](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory)). Default is on, with free writes (`write_approval: false` default). A Hermes deployment for Cortex would have to disable it and *keep* it disabled across weekly releases. |
| 2 | **Sessions** | **No. Structural leak — DISQUALIFIER.** | The gateway cannot run without its `SessionStore`: session keys are constructed inside the adapter (`build_session_key`), the runner injects the store into every adapter (`adapter.set_session_store(...)`, [run.py](https://github.com/NousResearch/hermes-agent/blob/main/gateway/run.py)), the Slack adapter writes session records itself (`_seed_assistant_thread_session`), and every message is persisted to `~/.hermes/state.db` (SQLite + FTS5) ([session-storage doc](https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage)). Hermes *is* the system of record for Slack conversation state in any deployment; the MCP server mode is explicitly a poller over that DB. This also directly concerns #459 (retention/trust boundary): all ledger-adjacent conversation content would be duplicated into a Hermes-owned store. |
| 3 | **Prompts** | **No, in every supported mode — DISQUALIFIER.** | Whatever responds to Slack is Hermes' `AIAgent`, whose system prompt is assembled by Hermes (`agent/prompt_builder.py`, SOUL persona files, per-channel `channel_prompts` in gateway config — [architecture doc](https://hermes-agent.nousresearch.com/docs/developer-guide/architecture), [base.py `resolve_channel_prompt`](https://github.com/NousResearch/hermes-agent/blob/main/gateway/platforms/base.py)). Steering the ledger console's behavior would mean writing Cortex's prompts *as* Hermes SOUL/skills/channel-prompt artifacts — prompt ownership moves into Hermes by construction. The #458 shape ("Cortex MCP tools exposed through Hermes") makes Hermes' loop the decider of when/how Cortex tools are called. |
| 4 | **Search** | **Not cleanly. Leak by duplication.** | Hermes ships its own search over everything it stores: FTS5 full-text search across all session messages (`messages_fts`, trigram variant) plus a `session_search` agent tool and memory substring search ([session-storage doc](https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage); [memory doc, "Session Search"](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory)). Cortex's ledger search would stay in Cortex, but a second, Hermes-owned search index over the same Slack conversations exists in any deployment, and Hermes' agent will answer "what did we decide?" questions from *its* transcript search unless explicitly prevented — a parallel answer surface that competes with the ledger. Cannot be disabled as a unit (it is the session store itself). |
| 5 | **Confidence** | **Yes, by construction.** | Hermes has no confidence-scoring concept anywhere in its gateway/agent surface (no such feature appears in the architecture, gateway, session, or memory docs; reviewed at the pinned commit). The leak risk is only prospective: if confidence display logic were implemented as Hermes skills/prompts it would migrate — same pathway as item 3, prevented by the same decision. |
| 6 | **Decision graph** | **Yes, by construction — same caveat.** | No decision-graph or ledger concept exists in Hermes. But under the #458 shape, Hermes' agent loop becomes the mediator that decides when to query/update the graph via MCP tools — conversational *control flow* over the decision graph would be owned by Hermes' loop even though the data stays in Cortex. That control flow is exactly what the master plan calls the product core. |

**Boundary summary:** items 2 (sessions) and 3 (prompts) are unavoidable in every supported Hermes deployment and are therefore **named disqualifiers** under the #456/#457 ground rules. Item 4 (search) is a structural duplication that cannot be turned off independently. Items 1, 5, 6 are individually avoidable but only through ongoing configuration discipline against a default-on, weekly-release framework.

---

## 7. Maintenance risk

**Bolt baseline:** `slack-bolt` (Python) is Slack's official, supported SDK — MIT, v1.28.0 released 2026-04-06, 27 open issues+PRs, steady multi-year cadence ([bolt-python releases](https://github.com/slackapi/bolt-python/releases)); `python-slack-sdk` v3.42.0 released 2026-05-18 ([python-slack-sdk releases](https://github.com/slackapi/python-slack-sdk/releases)). Vendor-aligned: when Slack changes the platform, Bolt is the first thing updated. Known weakness: the Socket Mode silent-disconnect issue class cited in §2, some open for years — hence the watchdog requirement.

**Hermes** (GitHub API, 2026-06-10):

- **License:** MIT — clean, attribution-only. **Low risk.**
- **Velocity:** ~weekly releases since 2026-03 (v0.2.0 → v0.16.0 in 13 weeks; [releases](https://github.com/NousResearch/hermes-agent/releases)); 500–1,200 commits/week over the last 8 weeks (GitHub stats API). Still **pre-1.0** (`v0.16.0`), so interface stability is not promised.
- **Bus factor:** top contributor `teknium1` has 5,313 commits; #2 has 903 — a ~6× cliff (contributors API). Effectively one dominant maintainer plus a long community tail. **High concentration.**
- **Issue/PR pressure:** 6,445 open issues vs 4,036 ever closed; in the 30 days to 2026-06-10: ~5,037 issues opened, ~2,016 closed (net +3,000/month), with **13,206 open PRs** and 1,939 merged in the same window (GitHub search API). The project is drinking from a firehose; an outsider's gateway-level bug report competes with thousands.
- **Track record under stress:** the exact-pinning response to the PyPI worm (§5) shows competent crisis response; the RSS-leak monitor shows honest operational self-awareness. Neither changes the structural numbers above.

**Net:** for a gateway that must hold Slack credentials and run unattended for months, a pre-1.0, single-dominant-maintainer framework with a growing 6k-issue backlog is a materially worse maintenance bet than the vendor's own SDK with 27 open issues — independent of every other finding.

---

## 8. Risks, ranked

**HIGH**

1. **Structural core-boundary leak (sessions + prompts) in every supported deployment mode.** The gateway cannot run without Hermes' `SessionStore` and `AIAgent`/prompt assembly in the message path (§6 items 2–3, with source citations). Under the #456 ground rules these are two named disqualifiers, not tunable risks.
2. **A second product core by gravity.** Hermes is a complete agent product (memory, search, skills, persona, tool runtime). Each Stage 3 feature built "the easy way" (as a Hermes skill, SOUL edit, or channel prompt) migrates ledger behavior into Hermes — the exact failure mode #457 exists to prevent. Evidence: the only supported integration shapes (skills, MCP client tools, channel prompts) all route through Hermes' loop (§3, §4, §6).

**MEDIUM**

3. **Maintenance exposure:** pre-1.0 weekly releases, ~6× bus-factor cliff, net +3,000 issues/month, 13.2k open PRs (§7) — against Slack's officially maintained Bolt at v1.28 with 27 open items.
4. **Operational footprint and attack surface on the credential-holding box:** ~1 GB image, mandatory stateful volume, single-writer constraint, runtime lazy dependency installation, an LLM credential required in the gateway, and a framework-acknowledged RSS-growth concern (§5). On Railway this is an order-of-magnitude heavier service than a stateless Bolt worker.
5. **Data residency duplication feeding #459:** every Slack message the console sees is persisted into a Hermes-owned SQLite store with its own FTS index and retention behavior, parallel to Cortex's Postgres (§6 items 2 and 4).

**LOW**

6. **Transport regression risk on the Bolt-direct path** — the silent-disconnect issue class in the official SDK ([python-slack-sdk#1379](https://github.com/slackapi/python-slack-sdk/issues/1379) et al.). Mitigated by copying Hermes' watchdog + dedup patterns (§2); these become explicit requirements for the Stage 3 gateway.
7. **License/IP:** MIT both sides; copying the two resilience patterns requires only attribution.
8. **Stdio-only MCP server mode:** even if wanted, it can't be consumed across Railway service boundaries without extra plumbing (§4) — low because nothing in the recommended path needs it.

---

## 9. Input to #457

The Bolt default stands, and this spike's recommendation to #457 is to **confirm it**: build the Stage 3 Slack gateway directly on `slack-bolt` (Python, Socket Mode) as a thin stateless Railway worker that forwards events to Cortex's existing hosted core, and **disqualify Hermes for the gateway role as scoped**, because every supported Hermes deployment routes Slack traffic through Hermes' own agent core — making Hermes the owner of conversation sessions (its SQLite `SessionStore` is non-optional and adapter-integrated) and of prompts (its `AIAgent`/prompt-builder answers every message), two named disqualifiers, with its FTS session search adding a third, duplicative answer surface over ledger conversations. The decisive technical fact is that Hermes' Slack transport *is* slack-bolt underneath, so Hermes offers no transport capability Cortex cannot get from the default; its genuinely valuable additions — the 15-second socket-liveness watchdog and the TTL event-dedup cache, both mitigations for documented official-SDK weaknesses — are ~160 lines of MIT-licensed pattern that #458/#455 should port with attribution rather than adopt a ~1 GB, pre-1.0, bus-factor-1 framework to obtain. Consequence for #458: re-scope the prototype from "Cortex MCP tools exposed through Hermes" to "Cortex ledger tools behind a Bolt gateway" (keeping any Hermes experiment as a throwaway comparison at most), and carry the §6 sessions/search findings into #459 as confirmed evidence that a Hermes-mediated deployment would duplicate all ledger-adjacent Slack content into a Hermes-owned store outside Cortex's trust boundary.
