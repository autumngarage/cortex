# Hermes build-vs-borrow boundary decision

**Date:** 2026-06-10
**Resolves:** cortex#457 (gates cortex#458)
**Inputs:** [hermes-gateway-spike.md](./hermes-gateway-spike.md) (cortex#456),
[hermes-retention-assessment.md](./hermes-retention-assessment.md)
(cortex#459)
**Authority context:** `cortex_master_plan.md` (canonical 2026-06-09) — the
default is the official Slack SDK/Bolt; Hermes must clear a real evidence
bar to displace it; in every outcome Hermes never owns memory, sessions,
prompts, search, confidence, or the decision graph.

## Decision

**Build on Bolt. Do not use Hermes as the Slack gateway, in any
configuration.** Borrow exactly two of its resilience patterns as
MIT-attributed prior art (~160 lines): the 15-second Socket Mode watchdog
and TTL-based event dedup. Read `gateway/platforms/slack.py` as reference
material for reconnect handling, thread-session keying, and rate-limit
backoff while implementing on `slack-bolt` directly.

## Why (the evidence bar was not cleared — it was inverted)

Both research tracks independently disqualified Hermes, on different
grounds, with source-pinned citations (NousResearch/hermes-agent @
`a72bb03`):

1. **No transport gain.** Hermes's Slack transport *is* `slack-bolt`
   underneath. As a gateway it adds nothing Bolt lacks; it adds an agent
   runtime we would have to suppress.
2. **Structural ownership of sessions and prompts** — two named
   disqualifiers. The SQLite `SessionStore` is mandatorily wired into the
   Slack adapter (no off switch; JSONL fallback when SQLite is
   unavailable), and `AIAgent`'s prompt-builder answers every message —
   there is no relay-only mode. The non-negotiable list is violated in
   every supported deployment.
3. **Retention residue outside our trust boundary.** Default retention is
   indefinite (`auto_prune: False`, "accumulates … forever" per its own
   config comment); thread backfill persists up to ~30 bystander messages
   with no toggle; Slack user-ID redaction is hard-excluded; the local
   `hermes mcp serve` surface reads the retained plaintext store with no
   authorization. None of that sits behind `visibility.py`'s
   deny-by-default boundary, so `slack_channel_excluded` cannot reach it,
   and #410-grade deletion guarantees are unverifiable for the Hermes
   copy.
4. **Search duplication.** Hermes's FTS session search is a second answer
   surface over conversation history — exactly the broad-inputs /
   narrow-output violation #441 exists to prevent.
5. **Maintenance risk.** Bus factor ≈ 1 (one author with ~85% of
   commits), pre-1.0 weekly releases, ~13k open PRs, issue closure far
   behind open rate — poor substrate odds for a trust-boundary component.

Items the spikes marked *unverified* (steady-state RSS; whether any
undocumented flag suppresses the built-in agent) do not affect this
decision: the sessions/prompts ownership findings hold regardless, and
either alone is disqualifying by the master plan's rule.

## Consequences

- **cortex#458 is rescoped** from "prototype Cortex MCP tools exposed
  through Hermes to Slack" to "prototype the Bolt Socket Mode gateway
  skeleton for the Stage 3 ledger console" — stateless worker, watchdog +
  dedup patterns ported with attribution, every message handled by
  calling the Cortex ledger API, zero local persistence beyond delivery
  dedup TTLs. (Issue body updated 2026-06-10.)
- **cortex#455 (Stage 3 console)** plans against Bolt; its children keep
  the one-ledger-API contract with no gateway-owned state.
- **No fork option.** Forking Hermes to excise sessions/prompts was
  considered and rejected: the excision is the majority of the program,
  and the maintenance posture makes a fork a liability, not a shortcut.
- The borrowed patterns carry MIT attribution when ported; the borrow is
  code-reading scope only — no dependency on the Hermes package, per the
  standalone-boundary guardrail (`tests/test_standalone_boundary.py`).

## Risks accepted

- We re-implement Socket Mode operational hardening ourselves (~160
  lines, patterns documented in the spike). Mitigated by the borrowed
  prior art and Bolt's first-party support.
- If Slack's Socket Mode contract shifts, we track Bolt releases
  directly — judged strictly better than tracking a bus-factor-1
  wrapper around Bolt.
