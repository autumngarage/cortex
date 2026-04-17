#!/usr/bin/env bash
#
# scripts/cleanup-branches.sh — safe branch hygiene tool.
#
# Usage:
#   bash scripts/cleanup-branches.sh                        # dry-run (default)
#   bash scripts/cleanup-branches.sh --execute              # actually delete
#   bash scripts/cleanup-branches.sh --remote-too --execute  # also delete remote branches
#
# Safety guarantees:
#   - Default mode is DRY RUN.
#   - The current branch is never deleted.
#   - The default branch (main/master) is never deleted.
#   - Local branches use `git branch -d` (not `-D`), so unmerged work is safe.
#   - Remote branches only deleted in --remote-too mode, only if no open PR and fully merged.
#   - Worktree-checked-out branches are skipped.
#
set -euo pipefail

DRY_RUN=1
REMOTE_TOO=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --execute|-x)
      DRY_RUN=0
      shift
      ;;
    --remote-too)
      REMOTE_TOO=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      sed -n '3,16p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument '$1'" >&2
      exit 1
      ;;
  esac
done

if ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: 'gh' is not installed." >&2
  exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Resolve default branch dynamically.
DEFAULT_BRANCH="$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name' 2>/dev/null || echo main)"
CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"

echo "==> Fetching from origin and pruning stale tracking refs ..."
git fetch --prune origin

PROTECTED_BRANCHES=("$DEFAULT_BRANCH" main master HEAD)

is_protected() {
  local b="$1"
  for p in "${PROTECTED_BRANCHES[@]}"; do
    [ "$b" = "$p" ] && return 0
  done
  return 1
}

# Branches checked out by worktrees — skip these.
WORKTREE_BRANCHES="$(git worktree list --porcelain | awk '/^branch /{sub("refs/heads/", "", $2); print $2}')"

is_worktree_branch() {
  local b="$1"
  [ -z "$WORKTREE_BRANCHES" ] && return 1
  while IFS= read -r wb; do
    [ -z "$wb" ] && continue
    [ "$b" = "$wb" ] && return 0
  done <<< "$WORKTREE_BRANCHES"
  return 1
}

DEFAULT_REF="origin/$DEFAULT_BRANCH"

echo ""
echo "==> LOCAL branches"

LOCAL_BRANCHES="$(git for-each-ref --format='%(refname:short)' refs/heads/)"
MERGED_LOCAL=()
UNMERGED_LOCAL=()

while IFS= read -r branch; do
  [ -z "$branch" ] && continue
  is_protected "$branch" || [ "$branch" = "$CURRENT_BRANCH" ] || is_worktree_branch "$branch" && continue
  if git merge-base --is-ancestor "$branch" "$DEFAULT_REF" 2>/dev/null; then
    MERGED_LOCAL+=("$branch")
  else
    UNMERGED_LOCAL+=("$branch")
  fi
done <<< "$LOCAL_BRANCHES"

if [ "${#MERGED_LOCAL[@]}" -gt 0 ]; then
  echo ""
  echo "  Fully merged into $DEFAULT_BRANCH (safe to delete locally):"
  for b in "${MERGED_LOCAL[@]}"; do
    echo "    - $b"
  done
fi

if [ "${#UNMERGED_LOCAL[@]}" -gt 0 ]; then
  echo ""
  echo "  Has unique commits not on $DEFAULT_BRANCH (will NOT auto-delete):"
  for b in "${UNMERGED_LOCAL[@]}"; do
    AHEAD="$(git rev-list --count "$DEFAULT_REF..$b" 2>/dev/null || echo "?")"
    echo "    - $b ($AHEAD commits ahead)"
  done
  echo ""
  echo "  These need a human decision. Either:"
  echo "    (a) merge the work via PR and rerun cleanup, or"
  echo "    (b) git branch -D <name> manually if the work is abandoned"
fi

if [ "${#MERGED_LOCAL[@]}" -eq 0 ] && [ "${#UNMERGED_LOCAL[@]}" -eq 0 ]; then
  echo "  (nothing to clean up)"
fi

# REMOTE branches.
REMOTE_DELETABLE=()
REMOTE_HAS_PR=()
REMOTE_UNIQUE_NO_PR=()

if [ "$REMOTE_TOO" -eq 1 ]; then
  echo ""
  echo "==> REMOTE branches (--remote-too)"

  REMOTE_BRANCHES="$(git for-each-ref --format='%(refname:short)' refs/remotes/origin/ | sed 's@^origin/@@' | grep -v '^HEAD$' || true)"
  OPEN_PR_BRANCHES="$(gh pr list --state open --limit 200 --json headRefName --jq '.[].headRefName' 2>/dev/null || echo "")"

  has_open_pr() {
    local b="$1"
    [ -z "$OPEN_PR_BRANCHES" ] && return 1
    while IFS= read -r pr_branch; do
      [ -z "$pr_branch" ] && continue
      [ "$b" = "$pr_branch" ] && return 0
    done <<< "$OPEN_PR_BRANCHES"
    return 1
  }

  while IFS= read -r remote_branch; do
    [ -z "$remote_branch" ] && continue
    is_protected "$remote_branch" && continue
    if has_open_pr "$remote_branch"; then
      REMOTE_HAS_PR+=("$remote_branch")
      continue
    fi
    if git merge-base --is-ancestor "origin/$remote_branch" "$DEFAULT_REF" 2>/dev/null; then
      REMOTE_DELETABLE+=("$remote_branch")
    else
      REMOTE_UNIQUE_NO_PR+=("$remote_branch")
    fi
  done <<< "$REMOTE_BRANCHES"

  if [ "${#REMOTE_DELETABLE[@]}" -gt 0 ]; then
    echo ""
    echo "  Fully merged + no open PR (safe to delete remotely):"
    for b in "${REMOTE_DELETABLE[@]}"; do
      echo "    - origin/$b"
    done
  fi

  if [ "${#REMOTE_HAS_PR[@]}" -gt 0 ]; then
    echo ""
    echo "  Has an open PR (will NOT delete):"
    for b in "${REMOTE_HAS_PR[@]}"; do
      echo "    - origin/$b"
    done
  fi

  if [ "${#REMOTE_UNIQUE_NO_PR[@]}" -gt 0 ]; then
    echo ""
    echo "  Unique commits but no open PR (will NOT auto-delete):"
    for b in "${REMOTE_UNIQUE_NO_PR[@]}"; do
      AHEAD="$(git rev-list --count "$DEFAULT_REF..origin/$b" 2>/dev/null || echo "?")"
      echo "    - origin/$b ($AHEAD commits ahead)"
    done
    echo ""
    echo "  These need a human decision: open a PR, or git push origin --delete <name>"
  fi

  if [ "${#REMOTE_DELETABLE[@]}" -eq 0 ] && [ "${#REMOTE_UNIQUE_NO_PR[@]}" -eq 0 ]; then
    echo "  (no remote-only cleanup candidates)"
  fi
fi

# DRY-RUN guard.
if [ "$DRY_RUN" -eq 1 ]; then
  echo ""
  echo "==> Dry run. Pass --execute to actually delete the branches listed above."
  exit 0
fi

# EXECUTE.
echo ""
echo "==> Executing deletions ..."

if [ "${#MERGED_LOCAL[@]}" -gt 0 ]; then
  for b in "${MERGED_LOCAL[@]}"; do
    if git branch -d "$b" 2>&1; then
      echo "    deleted local: $b"
    else
      echo "    SKIPPED local (git refused — likely has unmerged commits): $b" >&2
    fi
  done
fi

if [ "$REMOTE_TOO" -eq 1 ] && [ "${#REMOTE_DELETABLE[@]}" -gt 0 ]; then
  REPO_SLUG="$(gh repo view --json nameWithOwner --jq '.nameWithOwner' 2>/dev/null || true)"
  for b in "${REMOTE_DELETABLE[@]}"; do
    if [ -n "$REPO_SLUG" ] && gh api -X DELETE "/repos/$REPO_SLUG/git/refs/heads/$b" >/dev/null 2>&1; then
      echo "    deleted remote: origin/$b"
    elif git push origin --delete "$b" 2>&1; then
      echo "    deleted remote (via git push fallback): origin/$b"
    else
      echo "    SKIPPED remote (both gh api and git push --delete failed): origin/$b" >&2
    fi
  done
fi

echo ""
echo "==> Done. Run without --execute next time to dry-run."
