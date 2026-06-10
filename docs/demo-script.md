# The 5-minute Cortex demo — simlab demo tenant

**What the customer sees:** a team's decisions answered with citations
(`cortex ask`), and a pull request quietly reversing one of those decisions
getting caught with the receipt attached (`cortex review`). Everything runs
against the standing **simlab demo tenant** — a synthetic payments service
(`clean-shop` archetype, scenario `clean-shop-retry-fixed-delay`), so no
real project data is ever on screen.

The demo tenant is seeded by `tests/simlab/seed_demo.py` under a fixed UUID
namespace (`…#simlab-demo`, tenant `20002900-d2a5-54a4-8612-03a1a600f191`)
— deliberately **not** the project-root-derived default identity, so the
same tenant exists on every machine and reseeding is an idempotent hosted
replay. The acceptance tests for this script live in
`tests/simlab/test_simlab_demo.py`.

## Prerequisites

- This repo checkout, with dev deps: `bash setup.sh --deps-only`
- `DATABASE_URL` pointing at the hosted Postgres (Railway `compass`), schema
  applied (`cortex.hosted.migrations.apply_schema` — already live)
- The hosted extra for any live-DB step: `uv run --extra hosted …`
- Optional, for the live review moment: the `claude` CLI on PATH
  (without it, seeding verifies the retrieval half and says so — visibly)

## Seed the demo tenant

One command; idempotent; safe to run minutes before the call:

```bash
DATABASE_URL='postgresql://…' uv run --extra hosted \
    python -m tests.simlab.seed_demo
```

What it does, through the real product verbs: materializes the synthetic
repo (deterministic git history), derives candidates (`cortex derive`
pipeline), confirms the two human-enumerated decisions from the scenario
spec (`cortex candidates confirm --by simlab-demo`), pushes to the hosted
ledger (`cortex push` — reseeds report `0 appended, N replayed`), then
verifies both demo moments below and prints the transcript. Budget: well
under two minutes; a reseed is a pure replay.

Keep the working directory it prints (or rerun with `--scenario-id` for a
different rail). The scenario's diff is the committed
`tests/simlab/scenarios/clean-shop-retry-fixed-delay.json` patch.

## Minute 1-2 — ask

> "Your team's decisions, answered with receipts."

```bash
DATABASE_URL='postgresql://…' uv run --extra hosted cortex ask \
    "what did we decide about webhook retries and backoff?" \
    --tenant-id 20002900-d2a5-54a4-8612-03a1a600f191 \
    --source-id 929f0c19-cb77-5e31-b75c-c7950fee95d6 \
    --path <seeded-repo-or-any-cortex-project>
```

What lands: **1 cited decision** — the confirmed retry-policy rule,
verbatim ("Webhook retries in `src/payments/retry.py` must use exponential
backoff with jitter; fixed delays are forbidden"), with its span citation
and the omitted-counts line. Point out the two refusals this surface makes
by design: no snapshot → refuse; nothing confirmed → honest "No cited
decision found" with a remediation, never a vibe.

## Minute 3-4 — the catch

> "Now watch a PR quietly reverse that decision."

Show the diff (fixed 0.5s delay replacing the backoff), then:

```bash
DATABASE_URL='postgresql://…' uv run --extra hosted cortex review \
    --diff clean-shop-retry-fixed-delay.diff \
    --tenant-id 20002900-d2a5-54a4-8612-03a1a600f191 \
    --source-id 929f0c19-cb77-5e31-b75c-c7950fee95d6 \
    --path <seeded-repo>
```

What lands: **1 advisory finding** — `contradicts-prior-decision`, tier
`confirmed_cited`, with the decision id, the citation, and a suggested
repair. Name the Stage 0 posture out loud: advisory only, findings never
change the exit code, blocking is unrepresentable.

(No `claude` CLI on the demo machine? The seeding step already verified the
deterministic half — the live decisions-for-diff retrieval returns exactly
the confirmed decision this diff contradicts — and printed it as
`retrieval-only`. The pitch line is unchanged: retrieval is the product,
the model only phrases the finding.)

## Minute 5 — the citation trail

> "Nothing here is generated prose — every claim has a receipt."

Walk the chain on screen, bottom-up:

1. The finding cites a **decision id + version** in the hosted ledger.
2. The decision cites **source spans** — exact byte offsets into
   `CLAUDE.md` of the synthetic repo, content-hashed.
3. The ledger row carries the **confirmation event** (`--by simlab-demo`):
   a human confirmed this decision into existence; candidates never answer.
4. The replay key (`model … prompt … snapshot …`) makes the whole answer
   reproducible against a named graph snapshot.

Close with the honesty ledger: drifted citations are excluded with a named
skip, over-budget packs flag manual review, superseded decisions are
status-filtered out of the evaluator's sight — every omission is counted at
its stage (the regression rails for all of this: `pytest tests/simlab/`).

## Reset / reseed

Reseeding is idempotent — rerun the seed command any time; the hosted
ledger reports replays, never duplicates:

```bash
DATABASE_URL='postgresql://…' uv run --extra hosted \
    python -m tests.simlab.seed_demo
```

The ledger is append-only by design, so there is nothing to delete between
demos. To rehearse a different rail, pass `--scenario-id` (any committed
scenario in `tests/simlab/scenarios/` whose spec confirms at least one
decision), or run the whole scripted suite offline:

```bash
uv run pytest tests/simlab/ -q
```
