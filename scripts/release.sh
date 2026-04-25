#!/usr/bin/env bash
#
# scripts/release.sh — cut a cortex release.
#
# Usage:
#   scripts/release.sh --patch   # default
#   scripts/release.sh --minor
#   scripts/release.sh --major
#
# Cortex carries its version in source files (no hatch-vcs), so the helper
# bumps `src/cortex/__init__.py`, `pyproject.toml`, the README's version
# refs, and regenerates `uv.lock`. The release commit goes direct to main
# with --no-verify; this is intentional and matches touchstone's pattern
# ("release is a meta-action, not user code"). The release-published event
# fires .github/workflows/release.yml, which auto-bumps the homebrew tap.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

bump="${1:---patch}"
case "$bump" in
  --major|--minor|--patch) ;;
  *) echo "ERROR: unknown bump arg: $bump (use --major, --minor, --patch)" >&2; exit 1 ;;
esac

branch="$(git rev-parse --abbrev-ref HEAD)"
[ "$branch" = "main" ] || { echo "ERROR: must be on main (currently $branch)" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "ERROR: working tree dirty" >&2; exit 1; }
git fetch --tags origin >/dev/null
[ "$(git rev-list --left-right --count origin/main...main)" = "0	0" ] || { echo "ERROR: local main out of sync with origin" >&2; exit 1; }

current_version="$(grep -oE '__version__ = "[0-9.]+"' src/cortex/__init__.py | grep -oE '[0-9.]+')"
IFS='.' read -r major minor patch <<< "$current_version"
case "$bump" in
  --major) major=$((major + 1)); minor=0; patch=0 ;;
  --minor) minor=$((minor + 1)); patch=0 ;;
  --patch) patch=$((patch + 1)) ;;
esac
new_version="${major}.${minor}.${patch}"
new_tag="v${new_version}"

echo "==> Current: v${current_version}"
echo "==> New:     ${new_tag}"

# Bump version in source.
sed -i '' "s/__version__ = \"${current_version}\"/__version__ = \"${new_version}\"/" src/cortex/__init__.py
sed -i '' "s/^version = \"${current_version}\"/version = \"${new_version}\"/" pyproject.toml

# Bump README version refs (the two Codex flagged on v0.2.5 → v0.2.6).
# Targets specific phrasing rather than blanket version replace, so changelog
# entries and historical references stay intact.
if [ -f README.md ]; then
  sed -i '' "s|CLI v${current_version}|CLI v${new_version}|g" README.md
  sed -i '' "s|currently on v${current_version}|currently on v${new_version}|g" README.md
fi

# Regenerate lockfile.
uv lock >/dev/null

# Stage + commit (--no-verify: bypass no-commit-to-branch hook for release).
git add src/cortex/__init__.py pyproject.toml uv.lock
[ -f README.md ] && git add README.md || true
git commit --no-verify -m "chore: release ${new_tag}"

git tag "$new_tag"
git push --no-verify origin main "$new_tag"
gh release create "$new_tag" --generate-notes

echo
echo "  ✓ Released ${new_tag}"
echo "  Tap bump is in flight via .github/workflows/release.yml"
echo "  Watch: gh run list --workflow=release.yml --repo autumngarage/cortex --limit 1"
echo "  Upgrade: brew update && brew upgrade cortex"
