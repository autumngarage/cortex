# Cortex — the pitch

Plain-language explanations for sharing the project. SPEC.md and the Protocol are the load-bearing artifacts; this doc is the human-facing summary. Four sections, shortest first:

1. [The one-liner](#the-one-liner)
2. [What it's for](#what-its-for)
3. [The vision](#the-vision)
4. [The interaction model — day in the life](#the-interaction-model--day-in-the-life)

---

## The one-liner

> **Cortex is a file-format standard for project memory — so your AI never has to ask *"where were we?"* again.**

Alternates by audience:

- **For developers:** *AGENTS.md, but for memory instead of instructions.*
- **For teams:** *A git-native, cross-tool standard for the reasoning layer around your code — so decisions survive sessions, tools, and teammates.*
- **For skeptics:** *Every AI coding tool is building its own proprietary memory. Cortex is the shared spec they should converge on instead.*
- **The existential version:** *Code answers what. Git answers when. Cortex answers why.*

---

## What it's for

Try this: open a new Claude Code or Cursor session on a project you've been working on for a month and ask *"where were we?"* It doesn't know. Every session starts cold. The code shows *what* exists and git shows *when* things changed, but nothing durable captures *why* — why we picked this architecture, what we already tried, which decisions are load-bearing.

People patch this with a hand-maintained `CLAUDE.md` or a `.cursor/rules/` folder, and it rots. Cursor shipped a "Memories" feature in 2025 and quietly removed it five months later. The community has been hand-building the same three-file `.brain/` folder in every repo. The problem is real; nobody has cracked it.

**Cortex is a standard for how projects remember — stored as plain Markdown in your git repo, so any AI tool can read and write the same memory.**

It defines one small folder (`.cortex/`) with six kinds of documents:

- **Doctrine** — load-bearing decisions (*why this architecture, why this scope*). Numbered, never edited. If a decision changes, you write a new entry that "supersedes" the old one.
- **Journal** — a running log of what happened and what you learned. Append-only; you don't rewrite history.
- **Plans** — active efforts, each with measurable success criteria.
- **Procedures** — how-to guides for recurring operations.
- **Map** and **State** — generated summaries of "what exists structurally" and "what's current."

It also defines a **protocol** — rules any AI agent follows: when to write to the journal (a test broke after passing, a dependency changed, a PR merged, a plan status changed), what invariants to respect (journal never rewritten, doctrine never deleted, every generated summary declares its sources). The AI drafts entries continuously as it works; the human reviews a small queue and decides what gets "promoted" from fleeting journal notes into permanent doctrine.

**Why the approach is different:**

- **Cross-tool by design.** Cursor in the morning, Claude Code in the afternoon, a human at night — all read and write the same `.cortex/` folder. Nothing is trapped in a vendor's cloud.
- **Auditable.** Every summary cites its sources. Staleness is surfaced, not hidden.
- **Humans stay in the loop.** AI proposes; humans promote. The memory improves by deliberate review, not drift.
- **It's a protocol, not a product.** Any tool can implement it. If one vendor ships multi-layer memory natively, the spec is what they converge on.

**Status, honestly.** CLI v0.2.3 ships — `brew tap autumngarage/cortex && brew install autumngarage/cortex/cortex && cortex init` works today (fully-qualified name side-steps an unrelated `cortex` in homebrew-core). `cortex init` is an interactive wizard that wires `.cortex/` imports into your `CLAUDE.md` / `AGENTS.md` and `.gitignore`. On any project with existing structure (a `principles/` directory, a `ROADMAP.md`, an `adr/` folder, etc.) `cortex init` scans first, prints a one-screen summary of what it found, then asks per file whether to absorb it into Doctrine or Plans — every imported entry cites the source via `Imported-from:` frontmatter so the source file remains canonical. Files matching no built-in pattern get a classify prompt and the answer persists to `.cortex/.discover.toml`. `cortex doctor` surfaces the Autumn Garage siblings (Touchstone and Sentinel) when they're installed and warns on unscoped LLM/API constraints in `CLAUDE.md`/`AGENTS.md` so downstream tools (like Sentinel's planner) can't apply runtime-only rules to toolchain config. The protocol is defined (SPEC v0.3.1-dev, unchanged), dogfooded in its own repo, and survives two rounds of multi-agent critique. Regeneration commands (`refresh-map`, `refresh-state`) and the fully interactive promotion flow are Phase C. Any project can adopt the pattern by running the CLI or by hand-authoring the files and telling its AI to follow the protocol, and more tools will learn to read and write it the same way over time.

**The bet:** that the answer to "AI projects lose memory" is a shared file-format standard, not another proprietary memory feature.

---

## The vision

We're in the middle of a quiet transition in software. A year ago, an AI-coding assistant was something you asked for autocomplete. Today, serious teams ship real code through Claude Code, Cursor, and agents that run overnight. The pace is real; the productivity is real.

But something is quietly breaking. Every session with these tools starts from zero. The code doesn't explain *why it is this way*. Chat context evaporates when the window closes. The human who made the decision is at lunch. The AI that made the decision doesn't remember. A month of reasoning — about which architecture, which trade-offs, which dead ends — lives nowhere.

Teams are feeling this as friction. Individual engineers patch it with hand-written `CLAUDE.md` files. Whole companies are starting to worry about it. Vendors know: Cursor shipped a "Memories" feature in 2025 and pulled it five months later. Anthropic keeps adding memory primitives to Claude Code. Nobody has solved it, and the crucial open question is *whose memory wins*.

**The bet Cortex is making is that the answer is nobody's — it's a shared standard.**

If every AI vendor ships its own memory, projects get ten silos. Memory becomes proprietary, lock-in gets worse, and switching tools means losing everything you learned on the previous one. The alternative — the one AGENTS.md hinted at, the one the community is reaching for with hand-rolled `.brain/` folders — is a file-format standard any tool can read and write. Memory that lives in your git repo, portable, auditable, yours.

Cortex is the attempt at that standard. Six small document types. A protocol that tells AI agents when to write to them. Three invariants that keep the history honest: append-only journal, immutable-with-supersede doctrine, every generated summary declaring its sources. The AI drafts continuously as it works; the human reviews a short queue and decides what becomes permanent.

**What it looks like when it works.**

You open a session on a project you last touched six weeks ago. The AI loads a small token-budgeted slice of the project's memory: current state, load-bearing decisions, active plans, recent journal entries. It answers *"where were we?"* with citations. You work. It writes as it works. At the end of the day you spend thirty seconds on a promotion queue — *yes, that's a real pattern, promote it to doctrine*; *no, skip*. Over months, the project gets sharper, not noisier. Over years, onboarding a new teammate (or a new AI) is a matter of pointing them at `.cortex/`.

You move between tools — Cursor one day, Claude Code the next, a terminal agent the day after. The memory is the same. You change teams and your replacement inherits your reasoning, not just your code. A decision made in a crashed chat three weeks ago is still in the journal, with the evidence that led to it.

**Why now.** AI-assisted development is early enough that the standards are unsettled. The next twelve months decide whether project memory becomes proprietary — fragmented across vendors, lost on every tool switch — or shared, portable, durable. Cortex is a bet on the second outcome: the same move the industry already made with AGENTS.md, extended from *instructions* to *memory*. A spec any tool can implement, lived out as files in a git repo.

Code answers *what*. Git answers *when*. Cortex is the layer that answers *why* — and makes it durable, auditable, and yours.

---

## The interaction model — day in the life

Three moments of interaction, almost all of them light.

### 1. One-time setup (~2 minutes)

Setup in any project looks like this:

```
brew tap autumngarage/cortex
brew install autumngarage/cortex/cortex    # qualified — avoids the unrelated Prometheus `cortex` in homebrew-core
cortex init
cortex doctor    # verify
```

Source installs work too: `uv tool install git+https://github.com/autumngarage/cortex.git`. You can also hand-author the `.cortex/` folder following [SPEC.md](../SPEC.md) § 2 and copying [`.cortex/protocol.md`](../.cortex/protocol.md) + [`.cortex/templates/`](../.cortex/templates/) from this repo — the CLI is a convenience around the file format, not a requirement.

Either way, the second step is the same — add one line to your `AGENTS.md` or `CLAUDE.md`:

```
@.cortex/protocol.md
@.cortex/state.md
```

That's it. The project now has a `.cortex/` folder and every agent that reads `AGENTS.md` inherits the protocol.

### 2. During work — mostly invisible

You work normally. Claude Code, Cursor, Aider, whatever. You don't type anything differently. The agent reads `.cortex/state.md` at session start (it's in context via the import) and it knows where you left off.

As you work, the agent writes to `.cortex/journal/` on its own — triggered by deterministic events:

- **Test broke after passing earlier** → writes an incident entry
- **Diff touched a doctrine file** → writes a decision entry
- **Dependency changed (`pyproject.toml`, `package.json`, etc.)** → writes an entry
- **A plan's status changed** → writes a plan-transition entry
- **PR merged** → writes a merge summary
- **Commit message matched patterns like `fix: ... regression`** → writes an entry

You see these as one-line tool-use notifications in the transcript (*"wrote journal/2026-04-18-auth-retry-flake.md"*). You don't have to stop. You don't have to approve each one. They're append-only notes, not decisions.

The agent also reads `.cortex/doctrine/` when it needs to remember *why* something is the way it is. If you ask *"why is the retry backoff exponential?"* — instead of guessing, it greps doctrine and cites `doctrine/0015-exponential-retry-backoff.md`.

### 3. The daily check-in — ~30 seconds

Once a day, or whenever you feel like it, you run the bare command:

```
$ cortex
Cortex — your-project   spec v0.3.1   state: fresh (regenerated 2h ago)

▸ 7 Journal entries since last check
▸ 3 promotion candidates (1 stale, 2 proposed)
▸ March 2026 digest overdue by 8 days

 [1] j-2026-04-18-auth-retry   [trivial]   3 entries say the same thing
     → Promote to doctrine/0015?  [y/n/view/defer/skip]: y

 [2] j-2026-04-17-test-scoping  [editorial] New pattern; no Doctrine covers
     → Promote to doctrine/0016?  [y/n/view/defer/skip]: skip

 [3] j-2026-03-22-flaky-ci      [stale, 17d] Re-proposed after 3 new entries
     → Promote to doctrine/0017?  [y/n/view/defer/skip]: y

Generate March 2026 digest now?  [y/n]: y

Anything else?
```

You press `y`, `n`, `skip`, or `view`. The whole interaction is 30 seconds. Skipped candidates surface again later with new evidence attached. Stale ones get flagged for another look.

That's the UX. That's *all* the UX.

### What the human controls

- **Every doctrine entry.** Nothing becomes "permanent" without a human pressing `y`.
- **What counts as significant.** Project-specific trigger thresholds are configurable (e.g., "journal every PR merge" vs. "only architecturally-significant ones").
- **Overrides.** You can always hand-author a journal entry, supersede a doctrine entry, kill a promotion candidate forever.

### What's automated

- **Writing the journal** (AI does it, triggered deterministically).
- **Detecting staleness** (`cortex doctor` warns when state is >24h old).
- **Surfacing candidates** (when a journal pattern repeats, it shows up in the queue).
- **Regenerating the Map and State** (`cortex refresh-*` in Phase C, on demand or scheduled).

### The key invariant

You can always answer *"where were we?"* by just asking. The next session starts where the last one ended — even across a crash, across tool switches (Cursor → Claude Code), across team members. Because everything is files in git and the agent reads them at session start.

### The single sentence version

> Install once, let the AI write the journal as it works, press `y` on a short queue once a day.

Everything else is the machinery that makes that interaction trustworthy — the invariants, the doctor checks, the seven-field provenance — but as a user, you only touch the surface.
