"""`cortex derive` — walk local sources, emit candidate ledger events.

Stage 0 issues #350 (scaffold) + #351/#352/#353 (repo-native extractors).
This command reconstructs the decision surface from sources a repo already
has. The pipeline shape — source walk → `SourceDocument` snapshot →
pluggable extractor → validated `LedgerEvent` (`EVENT_SCHEMA_VERSION`,
`CANDIDATE_PROPOSED`) → local SQLite replay-export store — is the #350
scaffold; the default extractor is now the deterministic repo-native
dispatcher in `cortex.hosted.extractors` (CLAUDE.md/AGENTS.md rules, ADRs
near-verbatim, CODEOWNERS ownership signals). Further extractors (#354-#357)
plug in through the same `CandidateExtractor` callable type; derive defines
no event shape of its own. `empty_extractor` remains available for callers
that want the walk without extraction.

Identity defaults (documented contract):

- ``--tenant-id`` / ``--source-id`` default to deterministic UUIDv5 values
  derived from the **resolved project root path** under
  ``DERIVE_UUID_NAMESPACE``. That namespace is itself a UUIDv5 of
  ``uuid.NAMESPACE_URL`` and the URL
  ``https://github.com/autumngarage/cortex#derive``, so the same checkout
  path always maps to the same tenant/source pair and re-runs stay
  idempotent without any stored configuration.

Envelope validation failures are never silently dropped: every error names
the offending source file, all failing sources are reported together, and no
events are persisted when any source fails (validate-all-then-persist, so a
failed run leaves the store untouched and recoverable).
"""

from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import click

from cortex.hosted.derive_store import (
    DeriveEventStore,
    DeriveStoreError,
    derive_store_path,
)
from cortex.hosted.extractors import ExtractorError, RepoNativeExtractor
from cortex.hosted.ledger_events import (
    EVENT_SCHEMA_VERSION,
    LedgerEvent,
    LedgerEventType,
    LedgerEventValidationError,
)
from cortex.hosted.provenance import ProvenanceValidationError, SourceDocument

DERIVE_UUID_NAMESPACE = uuid5(NAMESPACE_URL, "https://github.com/autumngarage/cortex#derive")
DEFAULT_SOURCE_RELATIVE_PATHS: tuple[str, ...] = (
    "CLAUDE.md",
    "AGENTS.md",
    "docs/adr",
    "docs/decisions",
)
# GitHub resolves at most one CODEOWNERS file, in this precedence order; the
# default walk ingests only the first match so non-effective copies cannot
# propose ownership decisions that are not in force.
DEFAULT_CODEOWNERS_RELATIVE_PATHS: tuple[str, ...] = (
    ".github/CODEOWNERS",
    "CODEOWNERS",
    "docs/CODEOWNERS",
)
DERIVE_DOCUMENT_TYPE = "repo-file"
# Scaffold attribution: real per-author provenance arrives with the git-aware
# extractors (#351-#357); until then the deriver itself is the recorded author.
DERIVE_AUTHOR_REF = "cortex-derive"

# Typed extension point for issues #351-#357: an extractor receives one
# immutable SourceDocument snapshot and returns zero or more CANDIDATE_PROPOSED
# LedgerEvents for it. The pipeline re-validates every returned event against
# the run's tenant/source/type/version before anything is persisted.
CandidateExtractor = Callable[[SourceDocument], Sequence[LedgerEvent]]


class DeriveSourceError(ValueError):
    """Raised when a derive source or its extracted events fail validation.

    The message always names the offending source file (one line per failure
    when aggregated) so nothing is silently dropped.
    """


@dataclass(frozen=True)
class DeriveRunResult:
    """Outcome of one derive run, for the CLI summary and tests."""

    source_files: tuple[Path, ...]
    events: tuple[LedgerEvent, ...]
    inserted: int
    ignored: int
    db_path: Path


def empty_extractor(document: SourceDocument) -> tuple[LedgerEvent, ...]:
    """Default scaffold extractor: zero candidates (real ones are #351-#357)."""

    _ = document
    return ()


def default_tenant_id(project_root: Path) -> str:
    """Deterministic tenant UUID for a repo path (see module docstring)."""

    return str(uuid5(DERIVE_UUID_NAMESPACE, f"tenant:{project_root.resolve()}"))


def default_source_id(project_root: Path) -> str:
    """Deterministic source UUID for a repo path (see module docstring)."""

    return str(uuid5(DERIVE_UUID_NAMESPACE, f"source:{project_root.resolve()}"))


def resolve_source_files(project_root: Path, explicit: Sequence[Path]) -> tuple[Path, ...]:
    """Resolve `--source` arguments (or the built-in defaults) to files.

    Explicit sources must exist — a missing path is an error, never a skip.
    Default sources are best-effort by design ("if present"). Directories
    expand to their ``*.md`` files recursively in sorted order so the walk is
    deterministic; duplicates collapse keeping first occurrence. CODEOWNERS
    defaults follow GitHub's single-file precedence
    (``DEFAULT_CODEOWNERS_RELATIVE_PATHS``): only the first existing location
    is ingested.
    """

    root = project_root.resolve()
    files: list[Path] = []
    if explicit:
        for raw in explicit:
            path = (raw if raw.is_absolute() else root / raw).resolve()
            if path.is_dir():
                expanded = sorted(p.resolve() for p in path.rglob("*.md") if p.is_file())
                if not expanded:
                    raise DeriveSourceError(f"{raw}: directory contains no markdown files")
                files.extend(expanded)
            elif path.is_file():
                files.append(path)
            else:
                raise DeriveSourceError(f"{raw}: source path does not exist")
        return tuple(dict.fromkeys(files))
    for relative in DEFAULT_SOURCE_RELATIVE_PATHS:
        path = root / relative
        if path.is_dir():
            files.extend(sorted(p.resolve() for p in path.rglob("*.md") if p.is_file()))
        elif path.is_file():
            files.append(path.resolve())
    for relative in DEFAULT_CODEOWNERS_RELATIVE_PATHS:
        path = root / relative
        if path.is_file():
            files.append(path.resolve())
            break
    return tuple(dict.fromkeys(files))


def run_derive(
    *,
    project_root: Path,
    source_files: Sequence[Path],
    tenant_id: str,
    source_id: str,
    extractor: CandidateExtractor,
    db_path: Path | None = None,
) -> DeriveRunResult:
    """Run the derive pipeline: snapshot, extract, validate, then persist.

    Validation is fail-closed and complete before any write: if any source
    fails, a `DeriveSourceError` aggregating every failure (one line per
    source) is raised and the store is not touched.
    """

    root = project_root.resolve()
    target_db = db_path if db_path is not None else derive_store_path(root)

    events: list[LedgerEvent] = []
    errors: list[str] = []
    for path in source_files:
        try:
            events.extend(
                _extract_candidates(
                    path,
                    project_root=root,
                    tenant_id=tenant_id,
                    source_id=source_id,
                    extractor=extractor,
                )
            )
        except DeriveSourceError as exc:
            errors.append(str(exc))
    if errors:
        raise DeriveSourceError("\n".join(errors))

    with DeriveEventStore(target_db) as store:
        outcome = store.append_events(events)
    return DeriveRunResult(
        source_files=tuple(source_files),
        events=tuple(events),
        inserted=outcome.inserted,
        ignored=outcome.ignored,
        db_path=target_db,
    )


def _extract_candidates(
    path: Path,
    *,
    project_root: Path,
    tenant_id: str,
    source_id: str,
    extractor: CandidateExtractor,
) -> tuple[LedgerEvent, ...]:
    rel = _relative_to_root(path, project_root)
    document = _load_source_document(
        path, rel=rel, tenant_id=tenant_id, source_id=source_id
    )
    try:
        extracted = tuple(extractor(document))
    except LedgerEventValidationError as exc:
        raise DeriveSourceError(f"{rel}: candidate event failed envelope validation: {exc}") from exc
    except ExtractorError as exc:
        raise DeriveSourceError(f"{rel}: {exc}") from exc
    for event in extracted:
        _require_candidate_event(event, rel=rel, tenant_id=tenant_id, source_id=source_id)
    return extracted


def _relative_to_root(path: Path, project_root: Path) -> Path:
    try:
        return path.relative_to(project_root)
    except ValueError as exc:
        raise DeriveSourceError(
            f"{path}: source must live inside the project root {project_root}"
        ) from exc


def _load_source_document(
    path: Path,
    *,
    rel: Path,
    tenant_id: str,
    source_id: str,
) -> SourceDocument:
    try:
        content = path.read_text(encoding="utf-8")
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except (OSError, UnicodeDecodeError) as exc:
        raise DeriveSourceError(f"{rel}: cannot read source file: {exc}") from exc
    try:
        return SourceDocument(
            tenant_id=tenant_id,
            source_id=source_id,
            document_type=DERIVE_DOCUMENT_TYPE,
            external_id=str(rel),
            permalink=str(rel),
            author_ref=DERIVE_AUTHOR_REF,
            # File mtime is a placeholder source timestamp for the scaffold;
            # git-derived commit timestamps arrive with the real extractors.
            # It never feeds document_hash, which is content-keyed.
            source_timestamp=modified_at,
            content=content,
        )
    except ProvenanceValidationError as exc:
        raise DeriveSourceError(f"{rel}: source document failed envelope validation: {exc}") from exc


def _require_candidate_event(
    event: LedgerEvent,
    *,
    rel: Path,
    tenant_id: str,
    source_id: str,
) -> None:
    """Fail closed on extractor output that strays from the one envelope.

    Extractors are pluggable callables, so the static type alone cannot be
    trusted at this boundary — each check names the offending source file.
    """

    if not isinstance(event, LedgerEvent):
        raise DeriveSourceError(
            f"{rel}: extractor returned {type(event).__name__}, not a LedgerEvent"
        )
    if event.event_type is not LedgerEventType.CANDIDATE_PROPOSED:
        raise DeriveSourceError(
            f"{rel}: extractor emitted event type {event.event_type.value!r}; "
            f"derive emits only {LedgerEventType.CANDIDATE_PROPOSED.value!r}"
        )
    if event.event_version != EVENT_SCHEMA_VERSION:
        raise DeriveSourceError(
            f"{rel}: extractor emitted event_version {event.event_version}, "
            f"expected {EVENT_SCHEMA_VERSION}"
        )
    if event.tenant_id != tenant_id:
        raise DeriveSourceError(
            f"{rel}: extractor emitted tenant_id {event.tenant_id}, "
            f"expected this run's tenant {tenant_id}"
        )
    if event.source_id != source_id:
        raise DeriveSourceError(
            f"{rel}: extractor emitted source_id {event.source_id}, "
            f"expected this run's source {source_id}"
        )


def _validated_uuid_option(value: str | None, *, option_name: str) -> str | None:
    if value is None:
        return None
    try:
        UUID(value)
    except ValueError as exc:
        raise click.BadParameter(f"{value!r} is not a UUID", param_hint=option_name) from exc
    return value


@click.command("derive", context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--source",
    "sources",
    multiple=True,
    type=click.Path(path_type=Path),
    help=(
        "Source file or directory (repeatable; directories expand to *.md "
        "recursively). Default when omitted: CLAUDE.md, AGENTS.md, docs/adr/, "
        "docs/decisions/, and the first CODEOWNERS found in GitHub precedence "
        "order (.github/, repo root, docs/) — each only if present."
    ),
)
@click.option(
    "--tenant-id",
    default=None,
    help=(
        "Tenant UUID for emitted events. Default: deterministic UUIDv5 of the "
        "resolved project root under the cortex-derive namespace "
        "(uuid5(NAMESPACE_URL, 'https://github.com/autumngarage/cortex#derive'))."
    ),
)
@click.option(
    "--source-id",
    default=None,
    help=(
        "Source UUID for emitted events. Default: deterministic UUIDv5 of the "
        "resolved project root under the same cortex-derive namespace as "
        "--tenant-id."
    ),
)
@click.option(
    "--path",
    "project_root",
    type=click.Path(file_okay=False, dir_okay=True, exists=True, path_type=Path),
    default=Path.cwd,
    show_default="current directory",
    help="Project root containing `.cortex/`.",
)
def derive_command(
    *,
    sources: tuple[Path, ...],
    tenant_id: str | None,
    source_id: str | None,
    project_root: Path,
) -> None:
    """Walk local sources and emit candidate decisions as ledger events.

    The pipeline (issue #350) reads each source into an immutable snapshot,
    runs the deterministic repo-native extractors (issues #351-#353:
    CLAUDE.md/AGENTS.md rules, ADRs near-verbatim, CODEOWNERS ownership
    signals — no model calls), validates every event against the hosted
    ledger envelope, and persists to the local replay-export store at
    ``.cortex/.index/derive-events.sqlite``. Source material that does not
    become a candidate is reported as dropped chatter with reason codes.
    Re-running over unchanged inputs is a no-op; deleting the store and
    re-running reproduces it exactly.
    """

    root = Path(project_root).resolve()
    cortex_dir = root / ".cortex"
    if not cortex_dir.exists():
        click.echo(f"error: {cortex_dir} does not exist; run `cortex init` first.", err=True)
        sys.exit(2)

    tenant = _validated_uuid_option(tenant_id, option_name="--tenant-id") or default_tenant_id(root)
    source = _validated_uuid_option(source_id, option_name="--source-id") or default_source_id(root)

    try:
        source_files = resolve_source_files(root, sources)
    except DeriveSourceError as exc:
        click.echo(f"error: {exc}", err=True)
        sys.exit(1)

    if not source_files and not sources:
        defaults = ", ".join(DEFAULT_SOURCE_RELATIVE_PATHS)
        click.echo(f"derive: no default sources found (looked for: {defaults})")

    extractor = RepoNativeExtractor()
    try:
        result = run_derive(
            project_root=root,
            source_files=source_files,
            tenant_id=tenant,
            source_id=source,
            extractor=extractor,
        )
    except (DeriveSourceError, DeriveStoreError) as exc:
        for line in str(exc).splitlines():
            click.echo(f"error: {line}", err=True)
        sys.exit(1)

    click.echo(
        f"derive: {len(result.source_files)} source file(s), "
        f"{len(result.events)} candidate event(s) "
        f"({result.inserted} inserted, {result.ignored} duplicate)"
    )
    # Dropped chatter is visible by contract (bounded_omission): every
    # non-candidate block carries a reason code, never a silent skip.
    if extractor.dropped:
        reason_counts = Counter(record.chatter.reason_code for record in extractor.dropped)
        summary = ", ".join(
            f"{reason} x{count}" for reason, count in sorted(reason_counts.items())
        )
        click.echo(f"dropped: {len(extractor.dropped)} chatter record(s) ({summary})")
    click.echo(f"store: {result.db_path}")
