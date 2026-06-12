# Cortex — the pitch

Plain-language explanations for sharing the project. The canonical scope
and anti-goals live in
[product/scope-and-anti-goals.md](./product/scope-and-anti-goals.md); the
end-to-end user journeys in
[product/customer-journeys.md](./product/customer-journeys.md); SPEC.md
and the Protocol remain the load-bearing artifacts for the file layer.
This doc is the human-facing summary. Five sections, shortest first:

1. [The one-liner](#the-one-liner)
2. [The incident](#the-incident)
3. [What Cortex is](#what-cortex-is)
4. [What's real today — and what isn't yet](#whats-real-today--and-what-isnt-yet)
5. [The vision](#the-vision)

---

## The one-liner

> **Cortex is a reviewer that remembers every decision your team has ever
> made — and tells you when an AI agent's pull request quietly
> contradicts one.**

Alternates by audience:

- **For developers:** *A decision ledger built from the places decisions
  actually get made, plus a PR reviewer that flags changes contradicting
  it — with a citation, never a vibe.*
- **For teams:** *Your team's authoritative intent, captured where it
  happens and enforced at the merge gate — regardless of which agent or
  human wrote the diff.*
- **For skeptics:** *Code review checks the code. CI checks the build.
  Nothing checks the context the agent reasoned from. Cortex is that
  check.*
- **The existential version:** *Code answers what. Git answers when.
  Cortex answers what the team decided — and tells you when your agent
  forgot.*

---

## The incident

An AI agent changed code in a way that contradicted something your team
had already decided.

Not broken code — plausible code. The tests passed. The diff read
cleanly. It just reintroduced the pattern you retired last quarter, or
quietly dropped the constraint that was the whole point of the design,
or "remembered" an API that was deleted in March. A reviewer burned an
afternoon catching it. Or didn't.

AI agents now write a fast-growing share of production code, and they
are fluent, fast, and confidently wrong about a project's own history.
The deeper issue: **nothing verifies what the agent believed the rules
were.** And the decisions an agent should be held to rarely live in any
one file — they get made in Slack threads, GitHub issues, PR reviews,
and meeting notes, where they decay and where no agent will ever look.
A team's authoritative intent has no home, so an agent contradicts it
for free.

---

## What Cortex is

**Cortex is the decision ledger plus the reviewer that enforces it.**

- **The ledger.** A provenance-first record of decisions — each one
  carrying its source, author, timestamp, link, and supersede state, so
  a later decision overturns an earlier one and the history stays
  honest. Capture is broad (code, PRs, instruction files, ADRs; later
  Slack and issues); confirmation is human — candidates never become
  decisions on a model's say-so.
- **The reviewer.** A soft evaluator that diffs every new change
  against the relevant slice of the ledger and leaves an inline comment
  the way a senior teammate who happened to remember would: *"this
  reverses what was decided in #eng-arch on 2026-05-12 [link]"*.
  Advisory by default — a wrong comment is ignored, not a blocked
  merge. Individual decisions earn the right to block only after their
  own measured precision proves out.
- **The open foundation.** Underneath both sits the `.cortex/` file
  protocol and reference CLI: an open, portable, git-native format for
  project memory. Plain Markdown in your repo, append-only journal,
  immutable-with-supersede doctrine, generated context that declares
  its sources. Any tool can read and write it; nothing is trapped in a
  vendor's cloud. The hosted product is a workflow, inference, and
  audit layer around those files — never a proprietary memory store.

The discipline that keeps this focused: **broad inputs, narrow output.**
Cortex ingests from anywhere decisions are made, but only ever acts
through a few sharp surfaces — the cited answer, the advisory finding,
the explicit "no cited decision found." It is deliberately not a chat
agent, not a code generator, not a wiki, not a dashboard. The full
scope contract, including what Cortex will not do even if asked, is
[product/scope-and-anti-goals.md](./product/scope-and-anti-goals.md).

---

## What's real today — and what isn't yet

Honesty section. The claims above split cleanly into shipped and
pre-launch:

**The local loop runs, but the catch was staged.** On 2026-06-10 the full loop ran end-to-end
against live infrastructure: `cortex derive` extracted decision
candidates from a real repo, a human confirmed two into the ledger,
`cortex ask` returned cited answers (and correctly refused to answer
from unconfirmed candidates), and the evaluator caught a deliberately planted
contradicts-prior-decision finding — with the right citation, after correctly
rejecting an unconfirmed twin. That is a mechanism proof, not product
validation; the organic-catch bar is still open. The transcript is
[walkthrough-pe0.md](./walkthrough-pe0.md). The refusals in that
transcript are the product working: no snapshot, no answer; no
confirmation, no citation; no citation, no finding.

**The file protocol and CLI are shipped and installable:**

```
brew install autumngarage/cortex/cortex   # fully qualified — homebrew-core has an unrelated `cortex`
cortex init
cortex doctor
```

Source installs work too: `uv tool install
git+https://github.com/autumngarage/cortex.git`. The local CLI
validates the file contract, compiles token-budgeted session manifests,
runs exact and ranked lookup, and surfaces stale generated context —
deterministically, no LLM required. It is free and stays free: it is
the funnel and the protocol.

**The hosted product is pre-launch.** The GitHub App (advisory PR
comments on every pull request) and the Slack ledger console (`@cortex
what did we decide about X?`) are the productized surfaces of the same
loop, currently being built in the open in this repo. Pricing shape is
documented ahead of launch in [HOSTED-PRICING.md](./HOSTED-PRICING.md):
platform entitlement plus metered credits for LLM-backed work,
deterministic checks included. The journeys from landing page to
install to payment are pinned issue-by-issue in
[product/customer-journeys.md](./product/customer-journeys.md).

How to compare it:

| Category | What it optimizes for | Why Cortex is different |
|---|---|---|
| AI code reviewer (CodeRabbit-style) | Correctness and style of the diff | Cortex reviews the *context the agent believed*, against a cross-source decision record a PR-bound tool cannot assemble. It composes alongside code review. |
| Memory bank / notes folder | A place to store notes | Cortex's ledger is provenance-first and enforced: decisions carry sources and supersede edges, and a reviewer acts on them at the merge gate. |
| RAG / retrieval | Fast lookup over a corpus | Retrieval stays subordinate to provenance and citation; an answer without a confirmed, cited decision is an explicit no-answer. |
| Agent framework | Planning and executing work | Cortex does not run agents or write code; it is the neutral check on the agents that do. |

---

## The vision

We're in the middle of a quiet transition in software. Serious teams now
ship real code through Claude Code, Cursor, and agents that run
overnight. The pace is real; the productivity is real. And something is
quietly breaking: the share of code written by authors who have never
read the team's history — and can't — keeps rising.

Code review was built to catch bad code from authors who shared the
team's context. It was never built to catch plausible code from authors
who lack it. That gap is structural, and it sits at the one chokepoint
no IDE or model vendor owns: the merge gate.

The bet Cortex is making: **agent-context integrity becomes a standard
category** — checked the way teams check formatting, types, and tests
today — and the standard is open, portable, and owned by the ecosystem
rather than trapped inside one vendor's IDE. If every AI vendor ships
its own memory, projects get ten silos and switching tools means losing
everything the team learned. The alternative is a file-format standard
any tool can read and write, with a ledger that lives in your repo —
portable, auditable, yours — and a reviewer that makes it enforceable.

**What it looks like when it works.** A decision gets made in passing —
a Slack thread, a PR review, an ADR. Cortex stages it; a human confirms
it with one click. Weeks later, an agent that never saw that thread
opens a PR that contradicts it, and Cortex comments with the citation.
The decision changes; someone posts the update; Cortex records the
supersede and stops flagging the dead rule. Every review, override, and
👍/👎 sharpens the evaluator — the ledger is worth more in month six
than on install, by construction.

Cortex's role is to be **the decision graph and the reviewer that
enforces it** — neutral across agents, auditable by citation, riding
the model curve, and better every day it runs.

Code answers *what*. Git answers *when*. Cortex answers *what the team
decided* — and makes that answer durable, cited, and enforceable.
