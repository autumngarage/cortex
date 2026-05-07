#!/usr/bin/env bash
#
# hooks/cortex-pr-merged-hook.sh — auto-draft a Cortex `pr-merged` Journal
# entry after a PR squash-merges to the default branch.
#
# Implements Cortex Protocol § 2 Tier-1 trigger T1.9 ("Pull request merged
# to the default branch"). When a project has both Touchstone and Cortex
# installed, this hook fires from `merge-pr.sh` immediately after the
# remote merge succeeds and the local default branch is synced. It shells
# out to `cortex journal draft pr-merged --no-edit`, captures the new
# entry's path on stdout, and ships it via a feature branch + auto-merge
# PR (NOT a direct push to the default branch — the project's
# `no-commit-to-branch` policy explicitly forbids that path).
#
# Activation contract (ALL of these must hold; otherwise silent exit 0):
#   1. Push target is the default branch (main or master). Resolved by
#      asking `gh repo view`.
#   2. The repo has a `.cortex/` directory at the same level as `.git/`.
#   3. The `cortex` CLI is on $PATH.
#   4. `.touchstone-config` has `cortex_pr_merged_hook=auto` or `=on`.
#      Default for newly-bootstrapped projects: `auto`. Value `off`
#      disables. Missing key is treated as `auto` (so projects that
#      haven't migrated yet still benefit when the other gates pass).
#   5. The most recent commit on the default branch is NOT itself an
#      auto-draft pr-merged entry. The hook recognizes its own output
#      and refuses to recurse (cortex#193). Detection signal: the merged
#      commit's subject matches `^docs\(journal\): auto-draft pr-merged
#      entry`. This is a deliberate, narrow false-positive: a human-
#      written journal commit with that subject is also skipped, but
#      that's correct (the human is journaling the merge themselves —
#      no auto-draft needed).
#
# Failure modes (no silent failures past activation):
#   - cortex missing mid-flow (between detection and exec): log to stderr
#     and exit 0 (degrade gracefully — don't fail the merge because the
#     CLI was uninstalled in a tiny race window).
#   - `cortex journal draft` exits non-zero: stderr surfaced, exit 1.
#   - Empty stdout (no path returned): stderr message, exit 1.
#   - Returned path doesn't exist after the call: stderr message, exit 1.
#   - `git commit` on the feature branch fails: stderr message, exit 1.
#   - `git push` of the feature branch fails: stderr message naming the
#     local branch the operator can ship manually, exit 1.
#   - `gh pr create` fails (gh missing, auth, branch protection refusing
#     auto-merge): stderr message naming the local branch with the
#     committed entry, exit 0. The original PR has already merged; this
#     is the journal step, and stranding the operator on a clean named
#     branch with the work preserved is the documented degrade path.
#
# Inputs (env, all optional):
#   TOUCHSTONE_MERGED_PR        — PR number to thread through to the
#                                 commit message (e.g. supplied by
#                                 merge-pr.sh after `gh pr merge`).
#   TOUCHSTONE_CORTEX_HOOK_DISABLE
#                               — set to 1/true/on to short-circuit even
#                                 when config says auto/on. Useful for
#                                 tests that want to verify a path
#                                 without firing the writer.
#   TOUCHSTONE_CORTEX_HOOK_SKIP_PUSH
#                               — set to 1/true/on to commit on the
#                                 feature branch but skip the push +
#                                 gh-pr-create chain. Test fixtures use
#                                 this to verify the local commit shape
#                                 without hitting a remote.
#   TOUCHSTONE_DEFAULT_BRANCH   — override the default-branch lookup
#                                 (the test fixture sets this so it
#                                 doesn't need a configured GitHub remote).
#
# Exit codes:
#   0 — fired and shipped (or queued for auto-merge); OR silently skipped
#       (inactive); OR cortex went missing mid-flow (graceful degrade);
#       OR gh unavailable / refused (entry preserved on a named branch
#       and the operator was told how to ship it).
#   1 — activated and a real local failure occurred (journal draft
#       failed, commit/push failed, missing path).
#
set -euo pipefail

log() { printf '%s\n' "$*" >&2; }

truthy() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    1 | true | yes | on) return 0 ;;
    *) return 1 ;;
  esac
}

# Read a flat key=value from .touchstone-config. Echoes the trimmed value
# on stdout. Empty output means "key absent or no value". Comment lines
# (starting with `#`) and blank lines are ignored. The final occurrence
# wins (matching the wider config-parser idiom in new-project.sh).
read_config_value() {
  local config_file="$1" key="$2"
  local line lhs rhs result=""
  [ -f "$config_file" ] || { printf ''; return 0; }
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#"${line%%[![:space:]]*}"}"
    case "$line" in '#'* | '') continue ;; esac
    case "$line" in *=*) ;; *) continue ;; esac
    lhs="${line%%=*}"
    lhs="${lhs#"${lhs%%[![:space:]]*}"}"
    lhs="${lhs%"${lhs##*[![:space:]]}"}"
    if [ "$lhs" = "$key" ]; then
      rhs="${line#*=}"
      rhs="${rhs#"${rhs%%[![:space:]]*}"}"
      rhs="${rhs%"${rhs##*[![:space:]]}"}"
      result="$rhs"
    fi
  done < "$config_file"
  printf '%s' "$result"
}

# Match the canonical auto-draft commit subject. Used both to recognize
# our own previous output (recursion guard, cortex#193) and to compose
# the new auto-draft commit message; keep both call sites in sync by
# reading the prefix from one constant.
AUTO_DRAFT_SUBJECT_PREFIX='docs(journal): auto-draft pr-merged entry'

resolve_default_branch() {
  if [ -n "${TOUCHSTONE_DEFAULT_BRANCH:-}" ]; then
    printf '%s' "$TOUCHSTONE_DEFAULT_BRANCH"
    return 0
  fi
  local resolved=""
  if command -v gh >/dev/null 2>&1; then
    resolved="$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name' 2>/dev/null || true)"
  fi
  if [ -z "$resolved" ]; then
    # Fall back to the local symbolic-ref of origin/HEAD; finally to "main".
    resolved="$(git symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||' || true)"
  fi
  printf '%s' "${resolved:-main}"
}

# 1. Detection — silent skip if any precondition fails.
PROJECT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$PROJECT_DIR" ]; then
  exit 0
fi

current_branch="$(git -C "$PROJECT_DIR" branch --show-current 2>/dev/null || true)"
default_branch="$(resolve_default_branch)"
if [ -z "$current_branch" ] || [ "$current_branch" != "$default_branch" ]; then
  exit 0
fi

if [ ! -d "$PROJECT_DIR/.cortex" ]; then
  exit 0
fi

# Project-level opt-out via env (test fixtures + emergency disable).
if truthy "${TOUCHSTONE_CORTEX_HOOK_DISABLE:-0}"; then
  exit 0
fi

config_value="$(read_config_value "$PROJECT_DIR/.touchstone-config" cortex_pr_merged_hook)"
case "$config_value" in
  off | OFF | Off) exit 0 ;;
  on | ON | On | auto | AUTO | Auto | "") ;; # default to auto when absent
  *)
    # Unknown value — treat as off but warn so the project can fix the
    # config without surprise behavior.
    log "cortex-pr-merged-hook: unknown cortex_pr_merged_hook='$config_value' (expected: auto|on|off); skipping."
    exit 0
    ;;
esac

# Recursion guard (cortex#193). The merge that fired this hook may have
# been the auto-draft from a prior invocation — that PR's squash-merge
# carries the same subject we'd use for a new auto-draft, and re-firing
# would generate an infinite chain of meta-PRs (auto-draft of an
# auto-draft of an auto-draft …). Inspect the most recent commit on the
# default branch and bail if it's already one of ours.
last_subject="$(git -C "$PROJECT_DIR" log -1 --format=%s HEAD 2>/dev/null || true)"
case "$last_subject" in
  "$AUTO_DRAFT_SUBJECT_PREFIX"*)
    # The merge that triggered us IS an auto-draft. Nothing to journal.
    # Silent exit 0 — this is expected behavior, not a failure.
    exit 0
    ;;
esac

if ! command -v cortex >/dev/null 2>&1; then
  # Detection passed (`.cortex/` exists, config is auto/on) but the CLI
  # is missing. The brief calls this a graceful-degrade case — don't
  # fail the merge over a missing optional tool.
  exit 0
fi

# 2. Activated path — from here on, errors are visible failures.

# Refuse to run on a dirty tree: we'd silently fold uncommitted user work
# into the auto-commit. The caller (merge-pr.sh) leaves a clean tree by
# the time we reach this point; if anything else changed, that's a real
# problem the operator should see.
if [ -n "$(git -C "$PROJECT_DIR" status --porcelain)" ]; then
  log "cortex-pr-merged-hook: working tree has uncommitted changes after merge; refusing to auto-draft (would fold user changes into the auto-commit)."
  exit 1
fi

# `cortex journal draft pr-merged --no-edit` writes the entry and prints
# the absolute path on stdout. We capture stdout to grab that path; we
# leave stderr untouched so any cortex-side warnings (gh not auth'd, etc)
# surface to the operator running the merge.
draft_stdout=""
draft_status=0
draft_stdout="$(cd "$PROJECT_DIR" && cortex journal draft pr-merged --no-edit)" \
  || draft_status=$?
if [ "$draft_status" -ne 0 ]; then
  log "cortex-pr-merged-hook: cortex journal draft pr-merged exited $draft_status."
  exit 1
fi

# Take the last non-empty stdout line as the path. cortex's draft command
# emits exactly one line (the absolute path) but a trailing newline or a
# warning printed by an upstream Python wrapper could appear; the most-
# recent line is the path.
candidate="$(printf '%s\n' "$draft_stdout" | awk 'NF{p=$0} END{print p}')"
if [ -z "$candidate" ]; then
  log "cortex-pr-merged-hook: cortex journal draft returned no path on stdout."
  exit 1
fi

if [ ! -f "$candidate" ]; then
  log "cortex-pr-merged-hook: returned path '$candidate' is not a regular file."
  exit 1
fi

# 3. Stage + commit on a feature branch (NOT the default branch — see
# cortex#194 and `principles/git-workflow.md`'s "Never commit on the
# default branch" rule). Then ship via a PR.
rel_path="${candidate#"$PROJECT_DIR"/}"
pr_suffix=""
branch_slug=""
if [ -n "${TOUCHSTONE_MERGED_PR:-}" ]; then
  pr_suffix=" for #${TOUCHSTONE_MERGED_PR}"
  branch_slug="${TOUCHSTONE_MERGED_PR}"
else
  # No source-PR number to thread through. Use a date+time slug for
  # branch uniqueness so concurrent merges don't collide.
  branch_slug="$(date -u +%Y%m%d-%H%M%S)"
fi
commit_message="${AUTO_DRAFT_SUBJECT_PREFIX}${pr_suffix}"
feature_branch="docs/journal-pr-${branch_slug}"

# If the branch already exists locally (rare — leftover from a previous
# failed run), pick a unique suffix so we don't `checkout -b` onto an
# existing ref.
if git -C "$PROJECT_DIR" show-ref --quiet --verify "refs/heads/${feature_branch}"; then
  feature_branch="${feature_branch}-$(date -u +%H%M%S)"
  log "cortex-pr-merged-hook: feature branch existed; using ${feature_branch} instead."
fi

if ! git -C "$PROJECT_DIR" checkout -q -b "$feature_branch"; then
  log "cortex-pr-merged-hook: git checkout -b '$feature_branch' failed."
  exit 1
fi

if ! git -C "$PROJECT_DIR" add -- "$rel_path"; then
  log "cortex-pr-merged-hook: git add '$rel_path' failed."
  # Best-effort return to default branch so we don't leave the operator
  # parked on a half-prepared feature branch.
  git -C "$PROJECT_DIR" checkout -q "$default_branch" 2>/dev/null || true
  exit 1
fi

# --no-verify is intentional. Other pre-commit hooks (codex-review,
# touchstone-validate) inspect the diff and run network calls; this is
# a deterministic auto-commit of a single template-shaped journal file
# generated by the cortex CLI a moment ago, so re-running them adds no
# safety and would slow every merge by minutes.
if ! git -C "$PROJECT_DIR" commit --no-verify -m "$commit_message" >/dev/null; then
  log "cortex-pr-merged-hook: git commit failed."
  git -C "$PROJECT_DIR" checkout -q "$default_branch" 2>/dev/null || true
  exit 1
fi

# Skip the push only when explicitly requested (tests use this to verify
# the local commit shape without hitting a remote).
if truthy "${TOUCHSTONE_CORTEX_HOOK_SKIP_PUSH:-0}"; then
  # Test path: leave the operator parked on the feature branch so the
  # fixture can inspect HEAD. Production paths always return to default.
  exit 0
fi

# 4. Push + open auto-merge PR. Failures here are degraded gracefully:
# the original PR has already merged, and the auto-draft is preserved
# locally on a named branch the operator can ship by hand.
push_failed=0
if ! git -C "$PROJECT_DIR" push -u --no-verify origin "$feature_branch" >/dev/null 2>&1; then
  push_failed=1
fi

if [ "$push_failed" -eq 1 ]; then
  log "cortex-pr-merged-hook: failed to push '${feature_branch}' to origin."
  log "  The auto-draft entry committed locally as ${rel_path} on branch ${feature_branch}."
  log "  Ship it manually with: git push -u origin ${feature_branch} && gh pr create"
  git -C "$PROJECT_DIR" checkout -q "$default_branch" 2>/dev/null || true
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  log "cortex-pr-merged-hook: 'gh' not on PATH; auto-draft branch '${feature_branch}' pushed but no PR opened."
  log "  Open the PR manually with: gh pr create --title $(printf %q "$commit_message")"
  git -C "$PROJECT_DIR" checkout -q "$default_branch" 2>/dev/null || true
  exit 0
fi

# Compose the PR body.
pr_body_source_line=""
if [ -n "${TOUCHSTONE_MERGED_PR:-}" ]; then
  pr_body_source_line="Source PR: #${TOUCHSTONE_MERGED_PR}"$'\n\n'
fi
pr_body="${pr_body_source_line}Auto-drafted by \`cortex-pr-merged-hook\` after the source PR merged. Implements Cortex Protocol section 2 Tier-1 trigger T1.9."

# Best-effort label. The repo may not have the label configured — that's
# fine, fall through and try without it. We probe with `gh label list`
# rather than relying on `gh pr create --label` to fail and retry,
# because the failure mode of the latter is to abort PR creation entirely.
label_args=()
if gh label list --limit 200 --json name --jq '.[].name' 2>/dev/null \
    | grep -qx 'cortex-auto-draft'; then
  label_args=(--label cortex-auto-draft)
fi

pr_create_status=0
pr_url="$(gh pr create \
  --title "$commit_message" \
  --body "$pr_body" \
  --head "$feature_branch" \
  --base "$default_branch" \
  "${label_args[@]}" 2>&1)" || pr_create_status=$?

if [ "$pr_create_status" -ne 0 ]; then
  log "cortex-pr-merged-hook: 'gh pr create' failed (exit ${pr_create_status})."
  log "  gh output: ${pr_url}"
  log "  The auto-draft entry committed locally as ${rel_path} on branch ${feature_branch}."
  log "  The branch is pushed; finish by running: gh pr create --head ${feature_branch}"
  git -C "$PROJECT_DIR" checkout -q "$default_branch" 2>/dev/null || true
  exit 0
fi

# Extract a PR number from the URL gh prints (last path segment).
pr_number="${pr_url##*/}"
pr_number="${pr_number%%[!0-9]*}"

if [ -z "$pr_number" ]; then
  log "cortex-pr-merged-hook: could not parse PR number from gh output: ${pr_url}"
  log "  The branch '${feature_branch}' has been pushed and a PR likely exists; merge it manually."
  git -C "$PROJECT_DIR" checkout -q "$default_branch" 2>/dev/null || true
  exit 0
fi

# Queue for auto-merge. NOT --admin (would skip required checks) and NOT
# `merge-pr.sh` (would recursively invoke this hook). `gh pr merge --auto`
# waits for required checks to pass server-side, then squash-merges.
merge_status=0
gh pr merge "$pr_number" --squash --delete-branch --auto >/dev/null 2>&1 \
  || merge_status=$?

if [ "$merge_status" -ne 0 ]; then
  log "cortex-pr-merged-hook: 'gh pr merge --auto' on #${pr_number} returned ${merge_status}."
  log "  PR opened at ${pr_url} but auto-merge could not be queued. Merge it manually."
  git -C "$PROJECT_DIR" checkout -q "$default_branch" 2>/dev/null || true
  exit 0
fi

# 5. Return to default branch and best-effort sync.
git -C "$PROJECT_DIR" checkout -q "$default_branch" 2>/dev/null || true
git -C "$PROJECT_DIR" pull --ff-only --quiet 2>/dev/null || true

exit 0
