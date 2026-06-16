"""Tests for ``scripts/merge-pr.sh`` PR-triggered review gate."""

from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
MERGE_SCRIPT = REPO_ROOT / "scripts" / "merge-pr.sh"


def _payload(
    *,
    head: str = "abc123",
    comments: list[dict[str, Any]] | None = None,
    reviews: list[dict[str, Any]] | None = None,
    threads: list[dict[str, Any]] | None = None,
    contexts: list[dict[str, Any]] | None = None,
    review_decision: str = "",
) -> dict[str, Any]:
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "headRefOid": head,
                    "reviewDecision": review_decision,
                    "reviews": {"nodes": reviews or []},
                    "reviewThreads": {"nodes": threads or []},
                    "comments": {"nodes": comments or []},
                    "commits": {
                        "nodes": [
                            {
                                "commit": {
                                    "oid": head,
                                    "statusCheckRollup": {
                                        "contexts": {"nodes": contexts or []}
                                    },
                                }
                            }
                        ]
                    },
                }
            }
        }
    }


def _cortex_comment(*, body: str, author: str = "compass-review") -> dict[str, Any]:
    return {
        "author": {"login": author},
        "body": body,
        "url": "https://github.example/pr/7#comment",
        "createdAt": "2026-06-16T00:00:00Z",
    }


def _run_gate(tmp_path: Path, payload: dict[str, Any], *, head: str = "abc123") -> subprocess.CompletedProcess[str]:
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    script = textwrap.dedent(
        f"""
        gh() {{
          case "$*" in
            "repo view --json defaultBranchRef --jq .defaultBranchRef.name")
              printf '%s\\n' main
              ;;
            "repo view --json nameWithOwner --jq .nameWithOwner")
              printf '%s\\n' acme/widgets
              ;;
            api\\ graphql*)
              cat {payload_path!s}
              ;;
            *)
              printf 'unexpected gh call: %s\\n' "$*" >&2
              return 1
              ;;
          esac
        }}

        TOUCHSTONE_MERGE_PR_SOURCE_ONLY=true
        source {MERGE_SCRIPT!s} 7
        PR_NUMBER=7
        PR_TRIGGERED_REVIEW_REQUIRED=true
        PR_TRIGGERED_REVIEW_TRUSTED_COMMENT_AUTHORS="compass-review,compass-review[bot]"
        PR_TRIGGERED_REVIEW_TRUSTED_REVIEW_AUTHORS="chatgpt-codex-connector,codex,codex[bot]"
        PR_TRIGGERED_REVIEW_TRUSTED_CHECK_NAMES="codex-review"
        pr_triggered_review_gate {head}
        """
    )
    return subprocess.run(
        ["bash", "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env={**os.environ},
    )


def test_clean_current_head_cortex_comment_passes(tmp_path: Path) -> None:
    body = "\n\n".join(
        [
            "<!-- cortex-review:pr=7:head=abc123 -->",
            "### Cortex reviewed this PR against 1 recorded decision",
            "#### No contradictions found",
            "Cortex looked and found nothing to flag.",
        ]
    )
    result = _run_gate(tmp_path, _payload(comments=[_cortex_comment(body=body)]))
    assert result.returncode == 0, result.stderr + result.stdout
    assert "clean Cortex review" in result.stdout


def test_finding_bearing_cortex_comment_blocks(tmp_path: Path) -> None:
    body = "\n\n".join(
        [
            "<!-- cortex-review:pr=7:head=abc123 -->",
            "### Cortex reviewed this PR against 1 recorded decision",
            "Cortex flagged **1 potential conflict** with recorded decisions:",
        ]
    )
    result = _run_gate(tmp_path, _payload(comments=[_cortex_comment(body=body)]))
    assert result.returncode == 1
    assert "reported findings" in result.stderr


def test_missing_trusted_artifact_blocks(tmp_path: Path) -> None:
    result = _run_gate(tmp_path, _payload())
    assert result.returncode == 1
    assert "no trusted current-head PR-triggered review artifact" in result.stderr


def test_trusted_approved_review_passes(tmp_path: Path) -> None:
    review = {
        "author": {"login": "codex"},
        "state": "APPROVED",
        "submittedAt": "2026-06-16T00:00:00Z",
        "body": "",
        "url": "https://github.example/pr/7#review",
        "commit": {"oid": "abc123"},
    }
    result = _run_gate(tmp_path, _payload(reviews=[review]))
    assert result.returncode == 0, result.stderr + result.stdout
    assert "approved review by codex" in result.stdout


def test_current_head_codex_commented_review_passes_without_threads(tmp_path: Path) -> None:
    review = {
        "author": {"login": "chatgpt-codex-connector"},
        "state": "COMMENTED",
        "submittedAt": "2026-06-16T00:00:00Z",
        "body": "### Codex Review\n\nNo inline suggestions.",
        "url": "https://github.example/pr/7#review",
        "commit": {"oid": "abc123"},
    }
    result = _run_gate(tmp_path, _payload(reviews=[review]))
    assert result.returncode == 0, result.stderr + result.stdout
    assert "Codex review by chatgpt-codex-connector completed" in result.stdout


def test_unresolved_review_thread_blocks_even_with_clean_comment(tmp_path: Path) -> None:
    body = "\n\n".join(
        [
            "<!-- cortex-review:pr=7:head=abc123 -->",
            "### Cortex reviewed this PR against 1 recorded decision",
            "#### No contradictions found",
        ]
    )
    thread = {
        "id": "thread-1",
        "isResolved": False,
        "isOutdated": False,
        "comments": {"nodes": [{"url": "https://github.example/pr/7#thread"}]},
    }
    result = _run_gate(
        tmp_path,
        _payload(comments=[_cortex_comment(body=body)], threads=[thread]),
    )
    assert result.returncode == 1
    assert "unresolved review thread" in result.stderr
