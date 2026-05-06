# Cortex Case Studies

Documented incidents and gate reports that shaped the protocol design or validate the production-readiness claim. Each entry is a pointer into the primary source — the case study file or journal entry where the full evidence lives.

This index covers two kinds of entries: **design-shaping incidents** (problems that motivated protocol features) and **gate-validation findings** (evidence gathered during the v0.9.0 dogfood gate that confirms the design works in the wild).

---

## Design-shaping incidents

### 2026-04-24 — Stale CLAUDE.md steered an agent to recommend the wrong install path

**Project:** conductor
**Source:** [`docs/case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md`](./case-studies/2026-04-24-stale-claude-md-steered-agent-wrong.md)

A stale claim in `conductor/CLAUDE.md` — "Homebrew tap planned for v0.1.0; not yet wired" — was still loaded at session start eight releases after the tap had shipped. An agent asked about the install path answered with confidence that Homebrew wasn't an option, and proposed the tap repo and formula as new work to scope — work that had already been done.

The incident validated three Cortex design decisions:
- **Tier 1 trigger T1.10** (release-event journal entry) — no journal entry had recorded the tap going live, so nothing in `.cortex/` could contradict the stale claim.
- **`cortex doctor --audit-instructions`** — the missing audit check that would have surfaced the contradiction between `CLAUDE.md` prose and the published tap artifact.
- **Documentation Ownership** — the fact "where does this project install from?" had no single canonical owner; two files made claims, the tap repo was the real source of truth, and nothing reconciled the three.

This incident is the design motivating case for the protocol's instruction-audit feature.

---

## Gate-validation findings (v0.9.0 dogfood gate)

### 2026-05-06 — Vesper install: stale template URL caught by audit-instructions

**Project:** vesper
**Gate PR:** `henrymodisett/vesper#167`
**Source:** [`.cortex/journal/2026-05-06-v090-behavioral-exit-bar-review-gate-exit-declared.md`](../.cortex/journal/2026-05-06-v090-behavioral-exit-bar-review-gate-exit-declared.md)

During the vesper dogfood install, `cortex doctor --audit-instructions` surfaced a genuine stale external claim in the vesper README: the `YOUR_USERNAME/vesper.git` template URL had never been replaced with the real repo URL. Ten audited claims; one confirmed stale. This is the conductor-incident failure mode — stale prose in a session-start-loaded file — caught structurally by the audit check rather than by a human noticing.

The finding confirms that the protocol feature motivated by the 2026-04-24 conductor incident catches the same class of problem in the wild.

### 2026-05-06 — Three-target dogfood gate exit declared

**Source:** [`.cortex/journal/2026-05-06-v090-behavioral-exit-bar-review-gate-exit-declared.md`](../.cortex/journal/2026-05-06-v090-behavioral-exit-bar-review-gate-exit-declared.md)

All seven behavioral exit-bar criteria met across three installs (conductor, touchstone, vesper). Nine dogfood-surfaced bugs filed and closed in four swarm PRs in approximately 25 minutes. Two new CI fixtures (fresh-clone acceptance, bare-repo degradation) made the install-contract promises permanent regressions. The engineering claim — "Cortex installs cleanly on real projects, surfaces external-claim drift, degrades visibly when siblings are absent, and answers 'where were we?' on a fresh clone" — is substantively true and now testable in CI.

See the journal entry for the full 7-criterion evidence table and the three known-limitation calls.
