#!/usr/bin/env bash
#
# scripts/cleanup-worktrees.sh — safe git worktree hygiene tool.
#
# Usage:
#   bash scripts/cleanup-worktrees.sh              # dry-run (default)
#   bash scripts/cleanup-worktrees.sh --execute    # remove clean candidates
#   bash scripts/cleanup-worktrees.sh --force      # remove candidates even if dirty
#
# Safety guarantees:
#   - Default mode is DRY RUN.
#   - The main worktree is never removed.
#   - The current worktree is never removed.
#   - Clean worktrees are removable only when their branch is merged or
#     tree-equivalent to the default branch, or when the branch is gone.
#   - Dirty worktrees are refused unless --force is explicit.
#   - git worktree prune is previewed before any actual prune.
#
set -euo pipefail

DRY_RUN=1
FORCE=0

usage() {
  awk 'NR>2 && !/^#/ { exit } NR>2 { sub(/^# ?/, ""); print }' "$0"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --execute|-x)
      DRY_RUN=0
      shift
      ;;
    --force)
      DRY_RUN=0
      FORCE=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument '$1'" >&2
      exit 1
      ;;
  esac
done

if ! REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  echo "ERROR: cleanup-worktrees.sh must run inside a git repository." >&2
  exit 1
fi

cd "$REPO_ROOT"

resolve_default_ref() {
  local origin_head ref branch
  origin_head="$(git symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null || true)"
  if [ -n "$origin_head" ]; then
    ref="$origin_head"
    ref="${ref#refs/remotes/}"
    if git rev-parse --verify --quiet "$ref" >/dev/null; then
      printf '%s\n' "$ref"
      return 0
    fi
  fi

  for branch in main master; do
    if git rev-parse --verify --quiet "origin/$branch" >/dev/null; then
      printf '%s\n' "origin/$branch"
      return 0
    fi
    if git rev-parse --verify --quiet "$branch" >/dev/null; then
      printf '%s\n' "$branch"
      return 0
    fi
  done

  echo "ERROR: could not resolve a default branch ref (origin/HEAD, main, or master)." >&2
  return 1
}

is_fully_applied() {
  local upstream="$1"
  local branch="$2"
  local base file

  base="$(git merge-base "$upstream" "$branch" 2>/dev/null)" || return 1
  [ -z "$base" ] && return 1

  while IFS= read -r -d '' file; do
    [ -z "$file" ] && continue
    git diff --quiet "$upstream" "$branch" -- "$file" 2>/dev/null || return 1
  done < <(git diff --name-only --no-renames -z "$base" "$branch" 2>/dev/null)

  return 0
}

DEFAULT_REF="$(resolve_default_ref)"
CURRENT_WORKTREE="$(git rev-parse --show-toplevel)"

WORKTREE_LIST="$(git worktree list --porcelain)"
MAIN_WORKTREE="$(printf '%s\n' "$WORKTREE_LIST" | awk '/^worktree /{print substr($0, 10); exit}')"

CANDIDATE_PATHS=()
FORCE_PATHS=()

echo "==> Worktrees"
echo "    default ref: $DEFAULT_REF"

current_path=""
current_head=""
current_branch=""

flush_worktree() {
  [ -n "$current_path" ] || return 0

  local branch_label dirty_status dirty_label reason removable branch_name
  branch_label="${current_branch:-detached}"

  if dirty_status="$(git -C "$current_path" status --porcelain 2>/dev/null)"; then
    if [ -n "$dirty_status" ]; then
      dirty_label="dirty"
    else
      dirty_label="clean"
    fi
  else
    dirty_label="missing"
  fi

  printf '  - path: %s\n' "$current_path"
  printf '    branch: %s\n' "$branch_label"
  printf '    head: %s\n' "${current_head:-unknown}"
  printf '    status: %s\n' "$dirty_label"

  removable=0
  reason=""

  if [ "$current_path" = "$MAIN_WORKTREE" ]; then
    reason="main worktree"
  elif [ "$current_path" = "$CURRENT_WORKTREE" ]; then
    reason="current worktree"
  elif [ "$dirty_label" = "dirty" ] && [ "$FORCE" -ne 1 ]; then
    reason="dirty; use --force to remove"
  elif [ "$dirty_label" = "missing" ]; then
    reason="missing; git worktree prune handles this"
  else
    if [ -z "$current_branch" ]; then
      if [ -z "$current_head" ] || [ "$current_head" = "unknown" ]; then
        reason="detached HEAD missing; investigate manually"
      elif git merge-base --is-ancestor "$current_head" "$DEFAULT_REF" 2>/dev/null; then
        removable=1
        reason="detached HEAD merged into default"
      elif is_fully_applied "$DEFAULT_REF" "$current_head"; then
        removable=1
        reason="detached HEAD tree-equivalent to default"
      else
        reason="detached HEAD has unique work; use --force to remove"
      fi
    else
      branch_name="${current_branch#refs/heads/}"
      if ! git show-ref --verify --quiet "refs/heads/$branch_name"; then
        removable=1
        reason="branch gone"
      elif git merge-base --is-ancestor "$branch_name" "$DEFAULT_REF" 2>/dev/null; then
        removable=1
        reason="branch merged into default"
      elif is_fully_applied "$DEFAULT_REF" "$branch_name"; then
        removable=1
        reason="branch tree-equivalent to default"
      else
        reason="branch has unique work"
      fi
    fi
  fi

  printf '    decision: %s\n' "$reason"
  if [ "$removable" -eq 1 ]; then
    CANDIDATE_PATHS+=("$current_path")
    if [ "$dirty_label" = "dirty" ]; then
      FORCE_PATHS+=("$current_path")
    fi
  fi
}

while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    worktree\ *)
      flush_worktree
      current_path="${line#worktree }"
      current_head=""
      current_branch=""
      ;;
    HEAD\ *)
      current_head="${line#HEAD }"
      ;;
    branch\ *)
      current_branch="${line#branch }"
      ;;
    "")
      flush_worktree
      current_path=""
      current_head=""
      current_branch=""
      ;;
  esac
done <<< "$WORKTREE_LIST"
flush_worktree

echo ""
echo "==> Prune preview"
git worktree prune --dry-run --verbose || true

if [ "${#CANDIDATE_PATHS[@]}" -eq 0 ]; then
  echo ""
  echo "==> No removable worktrees found."
  exit 0
fi

echo ""
echo "==> Removable worktrees"
for path in "${CANDIDATE_PATHS[@]}"; do
  echo "  - $path"
done

if [ "$DRY_RUN" -eq 1 ]; then
  echo ""
  echo "==> Dry run. Pass --execute to remove the clean candidates listed above."
  exit 0
fi

echo ""
echo "==> Removing worktrees"
for path in "${CANDIDATE_PATHS[@]}"; do
  if [ "$FORCE" -eq 1 ]; then
    git worktree remove --force "$path"
  else
    git worktree remove "$path"
  fi
  echo "    removed: $path"
done

echo ""
echo "==> Pruning stale worktree metadata"
git worktree prune --verbose

echo ""
echo "==> Done. Run without --execute next time to dry-run."
