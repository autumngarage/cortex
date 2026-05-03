#!/usr/bin/env bash
#
# scripts/merge-pr.sh — squash-merge a PR and clean up.
#
# Usage:
#   bash scripts/merge-pr.sh <pr-number>
#   bash scripts/merge-pr.sh <pr-number> --bypass-with-disclosure="<reason>"
#
# What this does:
#   1. Verifies the PR is open and mergeable.
#   2. Runs AI code review as a merge gate.
#   3. Squash-merges and deletes the remote branch.
#   4. Checks out the default branch and pulls the updated state.
#
# Exit codes:
#   0 — merged cleanly
#   1 — merge failed (PR not mergeable, conflicts, etc.)
#   2 — usage / environment error
#
set -euo pipefail

PR_NUMBER=""
BYPASS_REASON=""
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REVIEW_SCRIPT="$SCRIPT_DIR/codex-review.sh"
REVIEWED_HEAD_OID=""
BYPASS_REVIEW=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    --bypass-with-disclosure=*)
      BYPASS_REVIEW=true
      BYPASS_REASON="${1#*=}"
      shift
      ;;
    --bypass-with-disclosure)
      echo "ERROR: --bypass-with-disclosure requires a non-empty reason." >&2
      exit 2
      ;;
    --*)
      echo "ERROR: Unknown option: $1" >&2
      exit 2
      ;;
    *)
      if [ -n "$PR_NUMBER" ]; then
        echo "ERROR: Unexpected extra argument: $1" >&2
        exit 2
      fi
      PR_NUMBER="$1"
      shift
      ;;
  esac
done

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

BYPASS_REASON="$(trim "$(printf '%s' "$BYPASS_REASON" | tr '\r\n\t' '   ')")"

if [ -z "$PR_NUMBER" ] || ! [[ "$PR_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "Usage: bash scripts/merge-pr.sh <pr-number> [--bypass-with-disclosure=\"<reason>\"]" >&2
  exit 2
fi
if [ "$BYPASS_REVIEW" = true ] && [ -z "$BYPASS_REASON" ]; then
  echo "ERROR: --bypass-with-disclosure requires a non-empty reason." >&2
  exit 2
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: 'gh' is not installed." >&2
  exit 2
fi

# Resolve the default branch.
DEFAULT_BRANCH="$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name' 2>/dev/null || echo main)"

truthy() {
  case "$(printf '%s' "${1:-false}" | tr '[:upper:]' '[:lower:]')" in
    true|1|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

review_clean_marker_key() {
  local branch="$1"
  printf '%s' "$branch" | sed 's/[^A-Za-z0-9._-]/_/g'
}

review_clean_marker_file() {
  local branch="$1"
  printf '%s/%s.clean' \
    "$(git rev-parse --git-path touchstone/reviewer-clean)" \
    "$(review_clean_marker_key "$branch")"
}

marker_field() {
  local field="$1"
  local marker="$2"
  awk -F= -v key="$field" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$marker"
}

worktree_path_for_branch() {
  local branch="$1"
  local current_path=""
  local current_branch=""
  local line key value

  git worktree list --porcelain | while IFS= read -r line || [ -n "$line" ]; do
    if [ -z "$line" ]; then
      if [ "$current_branch" = "refs/heads/$branch" ]; then
        printf '%s\n' "$current_path"
        exit 0
      fi
      current_path=""
      current_branch=""
      continue
    fi

    key="${line%% *}"
    value="${line#* }"
    case "$key" in
      worktree) current_path="$value" ;;
      branch) current_branch="$value" ;;
    esac
  done

  if [ "$current_branch" = "refs/heads/$branch" ]; then
    printf '%s\n' "$current_path"
  fi
  return 0
}

branch_has_clean_review_marker() {
  local branch="$1"
  local head_oid="$2"
  local merge_base="$3"
  local marker marker_branch marker_head marker_merge_base
  marker="$(review_clean_marker_file "$branch")"
  [ -f "$marker" ] || return 1
  grep -q '^result=CODEX_REVIEW_CLEAN$' "$marker" || return 1
  marker_branch="$(marker_field branch "$marker")"
  marker_head="$(marker_field head "$marker")"
  marker_merge_base="$(marker_field merge_base "$marker")"
  [ "$marker_branch" = "$branch" ] \
    && [ "$marker_head" = "$head_oid" ] \
    && [ "$marker_merge_base" = "$merge_base" ]
}

sync_default_branch_after_merge() {
  local current_branch current_worktree default_worktree

  echo "==> Merged. Updating local $DEFAULT_BRANCH ..."
  current_branch="$(git rev-parse --abbrev-ref HEAD)"

  if [ "$current_branch" = "$DEFAULT_BRANCH" ]; then
    git pull --rebase
    return 0
  fi

  current_worktree="$(git rev-parse --show-toplevel)"
  default_worktree="$(worktree_path_for_branch "$DEFAULT_BRANCH" | head -n 1)"
  if [ -n "$default_worktree" ] && [ "$default_worktree" != "$current_worktree" ]; then
    echo "==> $DEFAULT_BRANCH is checked out in sibling worktree: $default_worktree"
    echo "==> Fast-forwarding that worktree after remote merge ..."
    if git -C "$default_worktree" pull --ff-only; then
      return 0
    fi
    echo "WARNING: PR #$PR_NUMBER merged remotely, but sibling worktree '$default_worktree' could not fast-forward." >&2
    echo "WARNING: Run this when convenient: git -C '$default_worktree' pull --ff-only" >&2
    return 0
  fi

  git checkout "$DEFAULT_BRANCH"
  git pull --rebase
}

print_bypass_banner() {
  cat <<EOF

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
!! BYPASSING REVIEWER GATE
!! reason: $BYPASS_REASON
!! This bypass is recorded on the PR and squash commit.
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

EOF
}

record_bypass_comment() {
  gh pr comment "$PR_NUMBER" --body "Reviewer bypassed via \`--bypass-with-disclosure\`. Reason: $BYPASS_REASON"
}

run_merge_review() {
  local current_branch default_base_ref local_head pr_head_branch pr_head_oid

  if ! pr_head_branch="$(gh pr view "$PR_NUMBER" --json headRefName --jq '.headRefName' 2>/dev/null)"; then
    echo "ERROR: Failed to resolve PR #$PR_NUMBER head branch." >&2
    exit 1
  fi
  if ! pr_head_oid="$(gh pr view "$PR_NUMBER" --json headRefOid --jq '.headRefOid' 2>/dev/null)"; then
    echo "ERROR: Failed to resolve PR #$PR_NUMBER head commit." >&2
    exit 1
  fi
  if [ -z "$pr_head_branch" ]; then
    echo "ERROR: PR #$PR_NUMBER head branch is empty." >&2
    exit 1
  fi
  if [ -z "$pr_head_oid" ]; then
    echo "ERROR: PR #$PR_NUMBER head commit is empty." >&2
    exit 1
  fi

  REVIEWED_HEAD_OID="$pr_head_oid"
  default_base_ref="origin/$DEFAULT_BRANCH"

  if [ "$BYPASS_REVIEW" = true ]; then
    echo "==> Refreshing $default_base_ref before reviewer bypass validation ..."
    if ! git fetch origin "+refs/heads/$DEFAULT_BRANCH:refs/remotes/origin/$DEFAULT_BRANCH"; then
      echo "ERROR: Failed to refresh $default_base_ref before reviewer bypass validation." >&2
      exit 1
    fi
    if ! git rev-parse --verify --quiet "$default_base_ref^{commit}" >/dev/null; then
      echo "ERROR: Could not verify $default_base_ref before reviewer bypass validation." >&2
      exit 1
    fi
    if ! git cat-file -e "$pr_head_oid^{commit}" 2>/dev/null; then
      echo "==> Checking out PR #$PR_NUMBER head ($pr_head_branch) for reviewer bypass validation ..."
      gh pr checkout "$PR_NUMBER" --detach
    fi
    local current_merge_base
    if ! current_merge_base="$(git merge-base "$default_base_ref" "$pr_head_oid" 2>/dev/null)"; then
      echo "ERROR: Could not compute merge base for PR #$PR_NUMBER head against $default_base_ref." >&2
      exit 1
    fi
    if ! branch_has_clean_review_marker "$pr_head_branch" "$pr_head_oid" "$current_merge_base"; then
      echo "ERROR: Refusing reviewer bypass for PR #$PR_NUMBER." >&2
      echo "       No prior clean review marker matches branch '$pr_head_branch' at head '$pr_head_oid' and merge base '$current_merge_base'." >&2
      echo "       Run the reviewer cleanly once before using --bypass-with-disclosure." >&2
      exit 1
    fi
    print_bypass_banner
    record_bypass_comment
    return 0
  fi

  if truthy "${SKIP_REVIEW:-${SKIP_CODEX_REVIEW:-false}}"; then
    echo "==> Skipping merge review because SKIP_REVIEW is set."
    return 0
  fi

  if [ ! -f "$REVIEW_SCRIPT" ]; then
    echo "==> Review script not found at $REVIEW_SCRIPT — skipping review."
    return 0
  fi

  echo "==> Refreshing $default_base_ref for merge review ..."
  if ! git fetch origin "+refs/heads/$DEFAULT_BRANCH:refs/remotes/origin/$DEFAULT_BRANCH"; then
    echo "ERROR: Failed to refresh $default_base_ref before merge review." >&2
    exit 1
  fi
  if ! git rev-parse --verify --quiet "$default_base_ref^{commit}" >/dev/null; then
    echo "ERROR: Could not verify $default_base_ref before merge review." >&2
    exit 1
  fi

  if [ -n "$(git status --porcelain)" ]; then
    echo "ERROR: Working tree has uncommitted changes; refusing to run review against an ambiguous tree." >&2
    exit 1
  fi

  current_branch="$(git rev-parse --abbrev-ref HEAD)"
  local_head="$(git rev-parse HEAD)"
  if [ "$current_branch" != "$pr_head_branch" ] || [ "$local_head" != "$pr_head_oid" ]; then
    echo "==> Checking out PR #$PR_NUMBER head ($pr_head_branch) for merge review ..."
    gh pr checkout "$PR_NUMBER" --detach
    local_head="$(git rev-parse HEAD)"
  fi

  if [ "$local_head" != "$pr_head_oid" ]; then
    echo "ERROR: Local review checkout does not match PR #$PR_NUMBER head commit." >&2
    echo "       expected: $pr_head_oid" >&2
    echo "       actual:   $local_head" >&2
    exit 1
  fi

  echo "==> Running merge review ..."
  CODEX_REVIEW_BASE="$default_base_ref" \
    CODEX_REVIEW_BRANCH_NAME="$pr_head_branch" \
    CODEX_REVIEW_FORCE=1 \
    CODEX_REVIEW_MODE=review-only \
    bash "$REVIEW_SCRIPT"
}

# 1. Sanity check the PR exists and is open.
if ! PR_STATE="$(gh pr view "$PR_NUMBER" --json state --jq '.state')"; then
  echo "ERROR: Failed to inspect PR #$PR_NUMBER state with gh." >&2
  exit 1
fi
if [ "$PR_STATE" != "OPEN" ]; then
  echo "ERROR: PR #$PR_NUMBER is not open (state: $PR_STATE)." >&2
  exit 1
fi

# 2. Check mergeability with retries (GitHub's status can lag after a push).
echo "==> Checking merge state for PR #$PR_NUMBER ..."
STATE=""
MERGEABLE=""
for attempt in 1 2 3 4 5; do
  MERGE_STATE="$(gh pr view "$PR_NUMBER" --json mergeStateStatus,mergeable --template '{{.mergeStateStatus}} {{.mergeable}}' 2>/dev/null || echo '')"
  STATE="${MERGE_STATE%% *}"
  MERGEABLE="${MERGE_STATE#* }"
  [ -n "$STATE" ] || STATE="UNKNOWN"
  [ -n "$MERGEABLE" ] || MERGEABLE="UNKNOWN"
  echo "    attempt $attempt: mergeStateStatus=$STATE mergeable=$MERGEABLE"
  if [ "$STATE" = "CLEAN" ] && [ "$MERGEABLE" = "MERGEABLE" ]; then
    break
  fi
  if [ "$STATE" = "DIRTY" ] || [ "$STATE" = "BEHIND" ]; then
    echo "ERROR: PR #$PR_NUMBER is $STATE — has conflicts or is out of date with base." >&2
    echo "       Rebase or resolve conflicts on the PR branch before merging." >&2
    exit 1
  fi
  sleep 3
done

if [ "$STATE" != "CLEAN" ] || [ "$MERGEABLE" != "MERGEABLE" ]; then
  echo "ERROR: PR #$PR_NUMBER is not cleanly mergeable (state=$STATE mergeable=$MERGEABLE)." >&2
  echo "       Inspect manually: gh pr view $PR_NUMBER --web" >&2
  exit 1
fi

# 3. Run AI review as the merge gate.
run_merge_review

# 4. Squash-merge and delete the branch.
echo "==> Squash-merging PR #$PR_NUMBER ..."
if [ -z "$REVIEWED_HEAD_OID" ]; then
  echo "ERROR: Cannot merge PR #$PR_NUMBER because no reviewed head commit was recorded." >&2
  exit 1
fi
gh_merge_exit=0
if [ "$BYPASS_REVIEW" = true ]; then
  gh pr merge "$PR_NUMBER" --squash --delete-branch --match-head-commit "$REVIEWED_HEAD_OID" \
    --body "Reviewer-bypass: $BYPASS_REASON" || gh_merge_exit=$?
else
  gh pr merge "$PR_NUMBER" --squash --delete-branch --match-head-commit "$REVIEWED_HEAD_OID" \
    || gh_merge_exit=$?
fi

# `gh pr merge --delete-branch` does the squash AND tries to delete the
# local feature branch. The local-delete fails when the branch is checked
# out in the current worktree (the common case for parallel-worktree work).
# When that happens, the remote merge succeeded server-side — only the
# local cleanup didn't. Verify by asking the API; if MERGED, treat as
# success with a warning so the script doesn't claim the PR failed.
if [ "$gh_merge_exit" -ne 0 ]; then
  pr_state="$(gh pr view "$PR_NUMBER" --json state --jq '.state' 2>/dev/null || echo "")"
  if [ "$pr_state" = "MERGED" ]; then
    echo "WARNING: gh pr merge exited $gh_merge_exit, but PR #$PR_NUMBER is MERGED on GitHub."
    echo "         Likely cause: local feature branch is checked out in the current worktree,"
    echo "         which blocks --delete-branch's local-delete step. Remote branch is gone."
    echo "         Run 'bash scripts/cleanup-worktrees.sh --execute' from $DEFAULT_BRANCH to tidy up."
  else
    echo "ERROR: gh pr merge exited $gh_merge_exit and PR #$PR_NUMBER is not MERGED." >&2
    exit "$gh_merge_exit"
  fi
fi

# 5. Sync local default branch.
sync_default_branch_after_merge

# 6. Cortex post-merge hook (T1.9). Fires only when the project meets the
# activation criteria documented in scripts/cortex-pr-merged-hook.sh.
# Activation is the hook's job — we always invoke and let it self-gate.
# The hook may produce a follow-up commit on the default branch; that
# commit is created with --no-verify so it doesn't recurse through this
# script's review gates. Failures inside the hook surface as visible
# stderr; we don't fail the overall merge over a journal-write hiccup.
CORTEX_HOOK_SCRIPT=""
for candidate_hook in \
  "$SCRIPT_DIR/cortex-pr-merged-hook.sh" \
  "$(git rev-parse --show-toplevel 2>/dev/null)/scripts/cortex-pr-merged-hook.sh"; do
  if [ -n "$candidate_hook" ] && [ -f "$candidate_hook" ]; then
    CORTEX_HOOK_SCRIPT="$candidate_hook"
    break
  fi
done

if [ -n "$CORTEX_HOOK_SCRIPT" ]; then
  hook_status=0
  TOUCHSTONE_MERGED_PR="$PR_NUMBER" bash "$CORTEX_HOOK_SCRIPT" || hook_status=$?
  if [ "$hook_status" -ne 0 ]; then
    echo "WARNING: cortex-pr-merged-hook exited $hook_status (see above)." >&2
    echo "         The PR merged cleanly; only the auto-draft journal step had a problem." >&2
  fi
fi

echo "==> Done."
