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

command -v gh >/dev/null 2>&1 || { echo "ERROR: gh CLI not installed (need it for gh release create)" >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "ERROR: gh not authenticated (run: gh auth login)" >&2; exit 1; }

branch="$(git rev-parse --abbrev-ref HEAD)"
[ "$branch" = "main" ] || { echo "ERROR: must be on main (currently $branch)" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "ERROR: working tree dirty" >&2; exit 1; }
git fetch --tags origin >/dev/null
[ "$(git rev-list --left-right --count origin/main...main)" = "0	0" ] || { echo "ERROR: local main out of sync with origin" >&2; exit 1; }

current_version="$(grep -oE '__version__ = "[0-9.]+"' src/cortex/__init__.py | grep -oE '[0-9.]+')"

# Guard against partial-state from a previous failed run: if the source
# version is ahead of the latest tag, the previous release didn't make
# it to GitHub. Bumping again would skip past the intended version.
latest_tag="$(git tag -l --sort=-v:refname 'v*' | head -1)"
latest_tag_version="${latest_tag#v}"
if [ "$current_version" != "$latest_tag_version" ]; then
  echo "ERROR: source is at v${current_version} but latest tag is ${latest_tag:-(none)}" >&2
  echo "       A previous release likely failed mid-flight. Recover with:" >&2
  echo "         gh release create v${current_version} --target main --generate-notes" >&2
  echo "       Then rerun this helper." >&2
  exit 1
fi

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

# Push the version-bump commit first; then let gh create the tag + release
# atomically server-side. If gh release create fails, the version-ahead-of-tag
# guard at the top of the script catches the partial state on the next run.
# Pin the release target to the just-pushed SHA so a concurrent commit on
# main can't get released instead.
git push --no-verify origin main
target_sha="$(git rev-parse HEAD)"
gh release create "$new_tag" --target "$target_sha" --generate-notes
git fetch --tags origin >/dev/null || true

echo
echo "  ✓ Released ${new_tag}"
echo "  Tap bump is in flight via .github/workflows/release.yml"
echo "  Watch: gh run list --workflow=release.yml --repo autumngarage/cortex --limit 1"
echo "  Upgrade: brew update && brew upgrade cortex"
