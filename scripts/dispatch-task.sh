#!/usr/bin/env bash
# Dispatch a delegated task to conductor exec in an isolated git worktree.
#
# Usage:
#   scripts/dispatch-task.sh <brief-file> [provider]
#
# Where <brief-file> is a markdown file under briefs/ (or any path) describing
# a self-contained work item, and [provider] is a conductor provider name
# (default: codex). The brief's filename — minus extension — becomes the
# branch and worktree name.
#
# Side effects:
#   - Creates a branch task/<brief-stem> off main if missing
#   - Adds a worktree at ../cortex-<brief-stem>
#   - Fires `conductor exec --with <provider> --sandbox workspace-write
#       --tools Read,Grep,Glob,Edit,Write,Bash --cwd <worktree>` with the
#       brief on stdin
#   - Streams session events to ~/.cache/conductor/sessions/<id>.ndjson
#
# After the agent exits, review the diff, run tests, and ship via
# `scripts/open-pr.sh --auto-merge` from inside the worktree.

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <brief-file> [provider]" >&2
  exit 2
fi

brief_path="$1"
provider="${2:-codex}"

if [[ ! -f "$brief_path" ]]; then
  echo "brief not found: $brief_path" >&2
  exit 2
fi

if ! command -v conductor >/dev/null 2>&1; then
  echo "conductor CLI not on PATH; install via brew install autumngarage/conductor/conductor" >&2
  exit 3
fi

repo_root="$(git rev-parse --show-toplevel)"
brief_abs="$(cd "$(dirname "$brief_path")" && pwd)/$(basename "$brief_path")"
brief_stem="$(basename "$brief_path" .md)"
branch="task/${brief_stem}"
worktree_path="${repo_root}/../cortex-${brief_stem}"

cd "$repo_root"

git fetch origin main --quiet

if git show-ref --verify --quiet "refs/heads/${branch}"; then
  echo "branch ${branch} already exists; reusing" >&2
else
  git branch "${branch}" origin/main
fi

if [[ -d "${worktree_path}" ]]; then
  echo "worktree ${worktree_path} already exists; reusing" >&2
else
  git worktree add "${worktree_path}" "${branch}"
fi

echo "→ dispatching ${brief_stem} to conductor (provider: ${provider})"
echo "  worktree: ${worktree_path}"
echo "  branch:   ${branch}"
echo

conductor exec \
  --with "${provider}" \
  --sandbox workspace-write \
  --tools Read,Grep,Glob,Edit,Write,Bash \
  --cwd "${worktree_path}" \
  --task-file "${brief_abs}" \
  --verbose-route
