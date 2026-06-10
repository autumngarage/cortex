# Customer journeys — install, setup, payment

**Date:** 2026-06-10
**Owns:** the end-to-end user scenarios (website → install → setup →
payment) that the roadmap sequencing serves. Strategy authority remains
`cortex_master_plan.md` (Obsidian); pricing mechanics remain
[docs/HOSTED-PRICING.md](../HOSTED-PRICING.md); this document
operationalizes them as journeys and pins every step to a roadmap issue
so a sequencing change that breaks a journey is visible.

## The product split (two installs, one product)

| | Local Cortex | Hosted Cortex |
|---|---|---|
| What | Open file protocol + reference CLI; derive/ask/confirm/evaluate run locally | GitHub + Slack workflow surface: webhooks in, advisory PR comments + cited Slack answers out, audit ledger |
| Install | `brew install autumngarage/cortex/cortex` (or `uv tool install`) | Click-install: GitHub App on repos (Stage 2), Slack app on a workspace (Stage 3) |
| Account | None | Tenant created at App install (#386) |
| Price | Free, forever — it is the funnel and the protocol | Platform plan + metered credits for AI work; BYOK passes token costs through (HOSTED-PRICING.md) |
| Source of truth | `.cortex/` files + git | Same — the hosted service is a workflow/inference/audit layer, never a proprietary memory store |

There is deliberately **no web dashboard for decisions** (broad inputs,
narrow output — #441; a browsable decision wiki is the forbidden surface,
#382). The website is marketing, docs, and two install buttons. The only
web-account surface a customer ever needs is billing/plan management,
which GitHub Marketplace supplies in the default path.

## Journey 1 — local, free, five minutes (PE-0; available at Stage 0 exit)

1. Land on the site → the narrow incident: *"An AI agent changed code in a
   way that contradicted something your team had already decided."*
2. `brew install autumngarage/cortex/cortex`
3. `cortex derive` in their repo → candidates from CLAUDE.md/AGENTS.md,
   ADRs, CODEOWNERS, commit/PR history (#350–#356)
4. `cortex candidates list` / `confirm` — human-confirmed writes (#359)
5. `cortex ask "what did we decide about retries?"` → cited answer or an
   honest "no cited decision found" (#381–#383)
6. No payment, no account, no server. Exit point: "want this on every PR
   automatically?" → Journey 2.

**Issue coverage:** complete (all shipped or in tonight's bundles).
**Gap:** none for the loop; the landing page itself is tracked below.

## Journey 2 — hosted via GitHub App (PE-2; Stage 2 exit)

1. Same landing page → **Install GitHub App** (or find it on GitHub
   Marketplace, #434/#384)
2. GitHub's install flow: pick org → pick repos → approve least-privilege
   permissions (#385)
3. App install webhook → tenant + repo rows provisioned automatically
   (#386); no signup form — GitHub identity *is* the account
4. Cold start: repo-native backfill runs (derive over the repo), strictly
   advisory-only candidates (#362) — the product is useful on the first
   PR without a curation session
5. First PRs: advisory comments with citations (#390), stable IDs and
   deduped reruns (#391/#392), per-repo config (#397)
6. **Payment moment:** free advisory tier on install (the funnel, per the
   business plan). Upgrade prompt appears in the comment footer + repo
   config when usage crosses the free credit grant: GitHub **Marketplace
   billing** as the default rail (existing billing relationship, #384),
   **direct Stripe** as the documented fallback if Marketplace approval
   stalls (#399). Plan shape: platform entitlement sized by **active
   contributors** (#400) + monthly **credit grant** consumed by LLM-backed
   work (HOSTED-PRICING.md); deterministic checks free; caps pause with
   confirmation, never silent overage.
7. Feedback loop: 👍/👎/replies captured against decision-version +
   model/prompt version (#393/#394) — the data asset.

**Issue coverage:** every step above is an existing Stage 2 / GTM issue.
**Note:** #400 (active-contributor sizing) and HOSTED-PRICING.md (credits)
compose as plan-tier × credit-grant; #400's deliverable is to finalize
that composition.

## Journey 3 — Slack console joins (PE-3; Stage 3 exit)

1. From the GitHub App's settings page (or site): **Add to Slack**
2. Slack OAuth → workspace install → the bot joins one channel (narrow
   scope, #455)
3. `@cortex what did we decide about X?` → cited answers; `@cortex we
   decided…` → staged candidate awaiting confirm (#489/#490); curation
   verbs (#491)
4. Same tenant, same ledger, no second memory system; Bolt gateway with
   zero gateway-owned state (#457 decision, #458 skeleton)
5. No separate payment — Slack rides the existing tenant's plan; its
   LLM-backed asks consume the same credit pool.

## Journey 4 — design partner (now → Stage 2)

Pre-Marketplace reality: partners arrive via warm referrals (#437), get
the expectations/privacy one-pager (#402) + legal surfaces (#442), install
from a private App listing using the playbook (#396), and pay via pilot
agreement or priced LOI — the #398 money-down gate — handled manually
(invoice/Stripe link), not Marketplace. This journey is the bridge that
exists *before* the website journey is public.

## Sequencing assertions (what this document pins)

1. **Free-local-first is load-bearing:** Journey 1 must work before
   Journey 2 markets it (Stage 0 gate before Stage 2 rollout — already
   the spine's order).
2. **#384 stays front-loaded** (Stage 0): Marketplace verification is the
   wall-clock long pole for Journey 2's payment rail; its fallback #399
   stays in GTM.
3. **No journey requires a decision dashboard** — any feature that adds
   one violates #441/#309 and should be rejected at review.
4. **Tenant provisioning is install-derived** (#386), never a signup
   form; if a future feature needs an account surface beyond
   Marketplace/Stripe billing, that is a new product decision, not an
   implementation detail.
5. **The journeys impose no resequencing** of the current wave plan; they
   confirm it. The one previously-untracked artifact is the landing page
   itself — filed as a GTM issue alongside this document.

## Reference patterns (CodeRabbit, noted 2026-06-10)

The closest market analog's install grammar is the bar Journey 2 must
meet: *Sign in with GitHub → pick repos → first PR reviewed minutes
later*, free for open source, per-seat pricing, configuration via an
in-repo YAML file. We adopt: the two-click install grammar,
instant-value-on-first-PR (our cold-start backfill plays that role), a
free open-source tier as funnel, and seat-language pricing ("active
contributors", #400). We deliberately diverge on one axis: CodeRabbit
centers a web dashboard; Cortex keeps GitHub/Slack as the only output
surfaces and the in-repo config file (#397) as the only knob — the
no-dashboard assertion above is a feature, not a gap.
