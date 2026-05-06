# Cortex v0.8.3 released — installable baseline for vesper dogfood

**Date:** 2026-05-06
**Type:** release
**Trigger:** T1.10
**Tag:** v0.8.3
**Cites:** plans/cortex-v1, journal/2026-05-04-v0.8.2-released, journal/2026-05-05-pr-merged-0900, journal/2026-05-05-pr-merged-0924

> v0.8.3 is a no-new-features patch that publishes the post-v0.8.2 fixes through Homebrew so the v0.9.0 vesper dogfood install exercises current `main`, not stale 0.8.2 behavior.

## Artifact

- **Kind:** GitHub Release + Homebrew tap
- **Location:** https://github.com/autumngarage/cortex/releases/tag/v0.8.3; `autumngarage/homebrew-cortex` formula now points at v0.8.3
- **Version:** v0.8.3
- **Tag:** v0.8.3
- **Release notes:** https://github.com/autumngarage/cortex/releases/tag/v0.8.3

## What shipped

Ten commits accumulated on `main` between v0.8.2 (2026-05-04) and v0.8.3 (2026-05-06). All ride along; none are user-facing breaking changes.

- `feat: prefer manifest agent guidance (#115) (#132)` — manifest output prefers `Load-priority: always` doctrine when budget is tight.
- `feat: migrate legacy state to refreshable form (#131)` — old hand-authored `state.md` files migrate cleanly into the refresh-state regenerable shape.
- `docs: remove stale gh_release config reference (#125) (#130)` — `docs/config-reference.md` no longer documents the removed `gh_release` audit key (filed as cortex#125 from the touchstone install).
- `test(doctor): assert stale-checkbox check honors Doctrine 0007 scope (#129)` — doctor's stale-checkbox detector now respects canonical-ownership scope.
- `fix: ignore archived plans in state readers (#128)` — archived plans no longer surface in `cortex next` / state readers.
- `fix(journal): strip body placeholders in pr-merged --no-edit drafts (#127)` — auto-drafted pr-merged journals no longer leak template `{{ ... }}` markers.
- `docs(journal): auto-draft pr-merged entry for #126` + `docs: record touchstone cortex install (#126)` — touchstone install record landed.
- `docs(journal): auto-draft pr-merged entry for #122` + `docs: record conductor cortex install (#122)` — conductor install record landed.

## Downstream docs this changes

The audit-instructions seed list — places that could go stale if a future release moves the artifact:

- `CLAUDE.md` — install command, version reference (currently no hard-coded version; the brew tap path is canonical, no edit needed for v0.8.3).
- `README.md` — quickstart references SPEC v0.5.0 / Protocol v0.3.0; release.sh did its sed pass on phrases like `CLI v0.8.2` if any existed.
- `autumngarage/homebrew-cortex` — formula `url` + `sha256` auto-bumped by `release.yml` → `bump-tap` job; verified completion before brew upgrade.
- `docs/PITCH.md` — no version mention, no edit.
- `.cortex/state.md` — needs regeneration after this entry lands so v0.8.3 is the current installed reality.

## Follow-ups (deferred to future work)

- [ ] Stale post-install message in the brew formula caveats: the Homebrew install message still says `Ships targeting SPEC v0.3.1-dev and Protocol v0.2.0` — current is SPEC v0.5.0 / Protocol v0.3.0. Resolved to: a fresh `cortex#NNN` issue to file from the v0.9.0 vesper dogfood pass (this is exactly the kind of stale external-claim drift the dogfood gate exists to catch).

(Per SPEC § 4.2, deferred items must resolve to another Plan, Journal entry, or Doctrine entry in the same commit as the release entry. The follow-up above resolves to the v0.9.0 dogfood pass that immediately follows this release; the issue will be filed by the install delegate against `autumngarage/cortex` and cited from the vesper baseline journal.)
