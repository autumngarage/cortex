#!/usr/bin/env bash
#
# scripts/cortex-journal-wire.sh — Cortex T1.9 journal staging helpers for
# open-pr.sh and merge-pr.sh.
#
# Project-owned (not listed in .touchstone-manifest). Source this file; do not
# execute directly.
#
set -euo pipefail

cortex_journal_wire_resolve_t19_mode() {
  local project_dir="$1"
  local config_file="$project_dir/.cortex/config.toml"
  if [ ! -f "$config_file" ]; then
    printf '%s' "post-merge-writer"
    return 0
  fi
  python3 - "$config_file" <<'PY' 2>/dev/null || printf '%s' "post-merge-writer"
import sys
import tomllib
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = tomllib.loads(path.read_text())
except OSError:
    print("post-merge-writer", end="")
    raise SystemExit(0)

journal = data.get("journal")
t1_9 = journal.get("t1_9") if isinstance(journal, dict) else None
mode = t1_9.get("mode", "post-merge-writer") if isinstance(t1_9, dict) else "post-merge-writer"
if mode not in {"stage", "post-merge-writer"}:
    print("post-merge-writer", end="")
else:
    print(mode, end="")
PY
}

cortex_journal_wire_cli_ready() {
  command -v cortex >/dev/null 2>&1 \
    && cortex journal post-merge --help >/dev/null 2>&1 \
    && cortex journal stage --help >/dev/null 2>&1 \
    && cortex journal verify --help >/dev/null 2>&1
}

# Return 0 when open-pr should auto-stage a pr-merged journal entry.
cortex_journal_wire_should_stage() {
  local project_dir="$1"
  local base_branch="$2"
  local default_branch="$3"
  local no_stage_journal="${4:-0}"

  [ "$no_stage_journal" -eq 0 ] || return 1
  [ "$base_branch" = "$default_branch" ] || return 1
  [ -d "$project_dir/.cortex" ] || return 1
  cortex_journal_wire_cli_ready || return 1
  [ "$(cortex_journal_wire_resolve_t19_mode "$project_dir")" = "stage" ] || return 1
  return 0
}

# Return 0 when merge-pr should verify a staged pr-merged entry exists.
cortex_journal_wire_should_verify() {
  local project_dir="$1"
  local base_branch="$2"
  local default_branch="$3"

  [ "$base_branch" = "$default_branch" ] || return 1
  [ -d "$project_dir/.cortex" ] || return 1
  cortex_journal_wire_cli_ready || return 1
  [ "$(cortex_journal_wire_resolve_t19_mode "$project_dir")" = "stage" ] || return 1
  return 0
}

# Stage a pr-merged entry on the source branch and fold it into the tip commit.
cortex_journal_wire_stage_for_pr() {
  local project_dir="$1"
  local pr_number="$2"

  local staged_path=""
  local stage_status=0
  staged_path="$(cd "$project_dir" && cortex journal stage --type pr-merged --pr "$pr_number")" \
    || stage_status=$?
  if [ "$stage_status" -ne 0 ]; then
    echo "ERROR: cortex journal stage --type pr-merged --pr $pr_number failed (exit $stage_status)." >&2
    return 1
  fi
  if [ -z "$staged_path" ] || [ ! -f "$staged_path" ]; then
    echo "ERROR: journal stage returned no file path for PR #$pr_number." >&2
    return 1
  fi

  local rel_path="${staged_path#"$project_dir"/}"
  rel_path="${rel_path#/}"

  if git -C "$project_dir" diff --quiet -- "$rel_path" 2>/dev/null \
    && git -C "$project_dir" diff --cached --quiet -- "$rel_path" 2>/dev/null \
    && git -C "$project_dir" ls-files --error-unmatch -- "$rel_path" >/dev/null 2>&1; then
    echo "==> pr-merged journal entry already committed on branch for PR #$pr_number"
    return 0
  fi

  echo "==> Staging pr-merged journal entry for PR #$pr_number ..."
  git -C "$project_dir" add -- "$rel_path"
  if ! git -C "$project_dir" diff --cached --quiet -- "$rel_path"; then
    git -C "$project_dir" commit --amend --no-edit
    if git -C "$project_dir" rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
      git -C "$project_dir" push --force-with-lease
    else
      echo "WARNING: journal entry amended locally but branch has no upstream; push skipped." >&2
    fi
    echo "==> Amended branch tip with pr-merged journal entry ($rel_path)"
  fi
  return 0
}

# Gate merge when stage mode is active and the entry is missing or polluted.
cortex_journal_wire_verify_before_merge() {
  local project_dir="$1"
  local pr_number="$2"
  local base_branch="$3"
  local default_branch="$4"

  if ! cortex_journal_wire_should_verify "$project_dir" "$base_branch" "$default_branch"; then
    return 0
  fi

  echo "==> Verifying staged pr-merged journal entry for PR #$pr_number ..."
  if ! (cd "$project_dir" && cortex journal verify --type pr-merged --pr "$pr_number"); then
    echo "ERROR: merge blocked — staged pr-merged journal entry missing or incomplete for PR #$pr_number." >&2
    echo "       Run: cortex journal stage --type pr-merged --pr $pr_number" >&2
    echo "       Or open the PR with: bash scripts/open-pr.sh --no-stage-journal (audit will flag later)" >&2
    return 1
  fi
  return 0
}
