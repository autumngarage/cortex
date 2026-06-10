"""Seed the standing simlab demo tenant on the hosted DB (cortex#522).

Materializes a simlab archetype, runs the real product verbs against it —
``cortex derive`` (library entrypoint, same pipeline the CLI calls),
``cortex candidates confirm``, ``cortex push``, ``cortex ask``, and the
``cortex review`` retrieval boundary — under a dedicated demo tenant, then
verifies the two demo moments:

1. **ask** — a cited answer to the demo question, live from the hosted DB.
2. **review** — the scenario diff retrieves the confirmed decision it
   contradicts (the deterministic half of the catch; the full ``cortex
   review`` CLI run is attempted when the ``claude`` CLI is on PATH, and
   reported as retrieval-only otherwise — visibly, never silently).

Identity: the demo tenant/source UUIDs derive from a fixed namespace
(``…#simlab-demo``) — NOT the project-root default — so the tenant is the
same on every machine and reseeding is an idempotent hosted replay (the
derive events are byte-deterministic and the confirm idempotency key is
content-keyed, so ``cortex push`` reports replays, never duplicates).

Confirmations here are scripted batch confirms of selectors a human
explicitly enumerated in the committed scenario spec — the same posture as
``cortex candidates triage --accept-refs`` (human-enumerated refs, no
auto-confirm heuristic anywhere).

Run it:

    DATABASE_URL='postgresql://…' uv run --extra hosted \\
        python -m tests.simlab.seed_demo
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from click.testing import CliRunner

from cortex.cli import cli
from cortex.commands.confirm import load_candidate_rows
from cortex.commands.review import build_live_candidate_pack, claude_cli_available
from cortex.hosted.derive_store import DeriveEventStore, derive_store_path
from tests.simlab.generator import derive_materialized, materialize_archetype
from tests.simlab.specs import (
    ScenarioSpec,
    SimlabSpecError,
    load_archetype_specs,
    load_scenario_specs,
)

# Fixed demo identity namespace: the standing demo tenant is the same UUID on
# every machine, and deliberately NOT the project-root-derived default.
SIMLAB_DEMO_NAMESPACE = uuid5(
    NAMESPACE_URL, "https://github.com/autumngarage/cortex#simlab-demo"
)

DEMO_SCENARIO_ID = "clean-shop-retry-fixed-delay"
DEMO_QUESTION = "what did we decide about webhook retries and backoff?"
DEMO_ACTOR = "simlab-demo"

_PUSH_EVENTS_RE = re.compile(
    r"events: (?P<appended>\d+) appended, (?P<replayed>\d+) replayed, "
    r"(?P<skipped>\d+) skipped, (?P<failed>\d+) failed"
)


class SeedDemoError(RuntimeError):
    """Raised when a seeding step fails; the message names the failing verb."""


def demo_tenant_id() -> str:
    return str(uuid5(SIMLAB_DEMO_NAMESPACE, "tenant:simlab-demo"))


def demo_source_id(archetype_id: str) -> str:
    return str(uuid5(SIMLAB_DEMO_NAMESPACE, f"source:{archetype_id}"))


@dataclass(frozen=True)
class SeedReport:
    """Everything one seeding run did, for assertions and the demo console."""

    tenant_id: str
    source_id: str
    scenario_id: str
    archetype_id: str
    derived_candidates: int
    confirmed_refs: tuple[str, ...]
    push_appended: int
    push_replayed: int
    push_skipped: int
    push_failed: int
    ask_output: str
    ask_cited: bool
    review_mode: str
    review_output: str
    review_caught: bool
    elapsed_seconds: float

    @property
    def reseed_was_replay(self) -> bool:
        return self.push_appended == 0 and self.push_replayed > 0


def _invoke(
    runner: CliRunner, args: list[str], *, dsn: str | None, step: str
) -> str:
    env = {"DATABASE_URL": dsn} if dsn is not None else {}
    result = runner.invoke(cli, args, env=env, catch_exceptions=False)
    output = (result.output or "") + (getattr(result, "stderr", "") or "")
    if result.exit_code != 0:
        raise SeedDemoError(f"{step} failed (exit {result.exit_code}):\n{output}")
    return output


def _resolve_confirm_refs(root: Path, scenario: ScenarioSpec) -> tuple[str, ...]:
    """Resolve the spec's human-enumerated confirm selectors to event refs."""

    with DeriveEventStore(derive_store_path(root)) as store:
        candidates, _statuses = load_candidate_rows(store.export_events())
    refs: list[str] = []
    for selector in scenario.confirm:
        matches = [c for c in candidates if selector in c.decision_text]
        if len(matches) != 1:
            raise SeedDemoError(
                f"confirm selector {selector!r} matched {len(matches)} candidates; "
                "demo seeding requires exactly one"
            )
        refs.append(matches[0].event_hash[:12])
    return tuple(refs)


def seed_demo(
    dsn: str,
    *,
    scenario_id: str = DEMO_SCENARIO_ID,
    question: str = DEMO_QUESTION,
    tenant_id: str | None = None,
    source_id: str | None = None,
    work_dir: Path | None = None,
    live_review: bool | None = None,
) -> SeedReport:
    """Seed (or idempotently reseed) the demo tenant; verify both demo moments.

    ``live_review=None`` auto-detects: the full ``cortex review`` CLI runs
    when the ``claude`` binary is on PATH, otherwise the deterministic
    retrieval boundary is verified and the mode is reported as
    ``retrieval-only``.
    """

    started = time.monotonic()
    scenarios = {spec.scenario_id: spec for spec in load_scenario_specs()}
    if scenario_id not in scenarios:
        raise SeedDemoError(f"unknown scenario {scenario_id!r}")
    scenario = scenarios[scenario_id]
    if not scenario.confirm:
        raise SeedDemoError(
            f"scenario {scenario_id!r} confirms no decisions; the demo needs "
            "at least one confirmed decision to cite"
        )
    archetype = load_archetype_specs()[scenario.archetype_id]
    tenant = tenant_id if tenant_id is not None else demo_tenant_id()
    source = source_id if source_id is not None else demo_source_id(archetype.archetype_id)

    if work_dir is None:
        owns_work_dir = True
        base = Path(tempfile.mkdtemp(prefix="simlab-demo-"))
    else:
        owns_work_dir = False
        base = work_dir
    try:
        repo = materialize_archetype(archetype, base / archetype.archetype_id)
        derive_outcome = derive_materialized(repo, tenant_id=tenant, source_id=source)

        runner = CliRunner()
        try:
            refs = _resolve_confirm_refs(repo.root, scenario)
            for ref in refs:
                _invoke(
                    runner,
                    [
                        "candidates",
                        "confirm",
                        ref,
                        "--by",
                        DEMO_ACTOR,
                        "--path",
                        str(repo.root),
                    ],
                    dsn=None,
                    step=f"cortex candidates confirm {ref}",
                )
        except SimlabSpecError as exc:
            raise SeedDemoError(str(exc)) from exc

        push_output = _invoke(
            runner, ["push", "--path", str(repo.root)], dsn=dsn, step="cortex push"
        )
        push_match = _PUSH_EVENTS_RE.search(push_output)
        if push_match is None:
            raise SeedDemoError(
                f"cortex push output did not carry the events arithmetic:\n{push_output}"
            )

        ask_output = _invoke(
            runner,
            [
                "ask",
                question,
                "--tenant-id",
                tenant,
                "--source-id",
                source,
                "--path",
                str(repo.root),
            ],
            dsn=dsn,
            step="cortex ask",
        )
        ask_cited = "cited decision" in ask_output and "No cited decision" not in ask_output

        diff_path = base / f"{scenario.scenario_id}.diff"
        diff_path.write_text(scenario.patch, encoding="utf-8")
        use_cli_review = claude_cli_available() if live_review is None else live_review
        if use_cli_review:
            review_mode = "cli"
            review_output = _invoke(
                runner,
                [
                    "review",
                    "--diff",
                    str(diff_path),
                    "--tenant-id",
                    tenant,
                    "--source-id",
                    source,
                    "--path",
                    str(repo.root),
                ],
                dsn=dsn,
                step="cortex review",
            )
            review_caught = "advisory finding" in review_output and (
                "0 advisory finding" not in review_output
            )
        else:
            # Deterministic half of the catch: the live decisions-for-diff
            # retrieval returns the confirmed decision this diff contradicts.
            review_mode = "retrieval-only"
            pack = build_live_candidate_pack(
                dsn=dsn,
                diff_text=scenario.patch,
                tenant_id=tenant,
                source_id=source,
            )
            hits = [
                candidate
                for candidate in pack.candidates
                if candidate.status == "confirmed"
                and any(
                    selector in candidate.decision_text for selector in scenario.confirm
                )
            ]
            review_caught = bool(hits)
            review_output = (
                f"retrieval-only verification: {len(pack.candidates)} candidate(s) "
                f"in the live pack, {len(hits)} confirmed decision(s) matching the "
                "scenario's confirm selectors (full `cortex review` needs the "
                "`claude` CLI on PATH)"
            )

        return SeedReport(
            tenant_id=tenant,
            source_id=source,
            scenario_id=scenario.scenario_id,
            archetype_id=archetype.archetype_id,
            derived_candidates=derive_outcome.candidate_count,
            confirmed_refs=refs,
            push_appended=int(push_match.group("appended")),
            push_replayed=int(push_match.group("replayed")),
            push_skipped=int(push_match.group("skipped")),
            push_failed=int(push_match.group("failed")),
            ask_output=ask_output,
            ask_cited=ask_cited,
            review_mode=review_mode,
            review_output=review_output,
            review_caught=review_caught,
            elapsed_seconds=time.monotonic() - started,
        )
    finally:
        if owns_work_dir:
            shutil.rmtree(base, ignore_errors=True)


def render_report(report: SeedReport) -> str:
    lines = [
        f"simlab demo tenant: {report.tenant_id}",
        f"source ({report.archetype_id}): {report.source_id}",
        f"scenario: {report.scenario_id}",
        f"derived: {report.derived_candidates} candidate(s); "
        f"confirmed: {', '.join(report.confirmed_refs)}",
        f"push: {report.push_appended} appended, {report.push_replayed} replayed, "
        f"{report.push_skipped} skipped, {report.push_failed} failed",
        "",
        "— demo moment 1: cortex ask —",
        report.ask_output.rstrip(),
        "",
        f"— demo moment 2: cortex review ({report.review_mode}) —",
        report.review_output.rstrip(),
        "",
        f"ask cited: {report.ask_cited}; review caught: {report.review_caught}; "
        f"elapsed: {report.elapsed_seconds:.1f}s",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seed the standing simlab demo tenant (cortex#522)."
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Hosted Postgres URL (default: the DATABASE_URL environment variable).",
    )
    parser.add_argument(
        "--scenario-id",
        default=DEMO_SCENARIO_ID,
        help=f"Scenario to seed and verify (default: {DEMO_SCENARIO_ID}).",
    )
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="Skip the live `cortex review` CLI even when `claude` is on PATH.",
    )
    args = parser.parse_args(argv)
    dsn = (args.database_url or os.environ.get("DATABASE_URL", "")).strip()
    if not dsn:
        print(
            "seed_demo: hosted ledger not configured; set DATABASE_URL (or pass "
            "--database-url) to the hosted Postgres (degradation: "
            "degraded_capability — nothing local is substituted for the ledger)",
            file=sys.stderr,
        )
        return 2
    try:
        report = seed_demo(
            dsn,
            scenario_id=args.scenario_id,
            live_review=False if args.retrieval_only else None,
        )
    except SeedDemoError as exc:
        print(f"seed_demo: error: {exc}", file=sys.stderr)
        return 1
    print(render_report(report))
    if not report.ask_cited or not report.review_caught:
        print(
            "seed_demo: a demo moment failed verification (see output above)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
