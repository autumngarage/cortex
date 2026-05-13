# Journal draft facts-file schema

Issue #243 introduces a narrow, provider-agnostic handoff contract for journal drafting:

- Input is a compact **facts packet** (JSON only).
- Output is a deterministic draft rendered from existing `.cortex/templates/journal/*.md` templates.
- The packet must not include broad context dumps (`.cortex/journal/**`, full source files, chat transcripts).

This contract is implemented in `src/cortex/journal_facts.py` as:

- `JSON_SCHEMA_DRAFT_2020_12` (reference JSON Schema)
- strict runtime validation (`validate_facts_packet`)

## CLI usage

```bash
cortex journal draft pr-merged --facts-file fixtures/valid-pr-merged.json
```

Current `--facts-file` rendering support:

- `pr-merged`
- `decision`
- `release`

Schema definitions are included for all Tier-1 event types:

- `pr-merged`
- `decision`
- `incident`
- `release`
- `plan-transition`

If validation fails, Cortex prints a structured JSON diagnostic to stderr, exits non-zero, and writes nothing.

## Top-level shape

```json
{
  "type": "pr-merged | decision | incident | release | plan-transition",
  "title": "string"
}
```

`type` + additional required fields vary by event type.

## Event examples

### `pr-merged`

```json
{
  "type": "pr-merged",
  "title": "feat(journal): facts-file handoff",
  "pr_number": 243,
  "branch": "feat/journal-facts-file",
  "commit_range": "a1b2c3d..d4e5f6a",
  "changed_files": [
    "src/cortex/commands/journal.py",
    "src/cortex/journal_facts.py",
    "tests/test_journal_draft.py"
  ],
  "diffstat": "3 files changed, 120 insertions(+), 5 deletions(-)",
  "behavior_summary": "Adds deterministic journal draft rendering from a compact facts packet.",
  "tests_run": [
    "uv run pytest tests/test_journal_draft.py"
  ],
  "cortex_refs": {
    "plans": ["context-integrity-production"],
    "doctrine": ["0001-why-cortex-exists"],
    "spec": ["§ 4.2", "§ 7"],
    "journal": ["2026-05-13-facts-file-track-a"]
  },
  "followups": [
    "Wire PR-merged hook to emit this facts schema."
  ]
}
```

### `decision`

```json
{
  "type": "decision",
  "title": "Prefer compact facts packet for journal drafting",
  "trigger": "T2.4",
  "summary": "Journal drafting now accepts a narrow facts packet.",
  "context": "Premium model sessions should not resend full corpus context for templated notes.",
  "decision": "Add strict schema validation and deterministic rendering for facts packets.",
  "action_items": [
    "Document schema examples for all Tier-1 event types.",
    "Add CLI tests for malformed input behavior."
  ],
  "cortex_refs": {
    "plans": ["context-integrity-production"],
    "spec": ["§ 4.2"]
  }
}
```

### `release`

```json
{
  "type": "release",
  "title": "Release v1.6.3 — facts-file support",
  "tag": "v1.6.3",
  "summary": "Ships deterministic facts-file journal drafting.",
  "artifact": {
    "kind": "GitHub Release",
    "location": "https://github.com/autumngarage/cortex/releases/tag/v1.6.3",
    "version": "1.6.3",
    "release_notes": "https://github.com/autumngarage/cortex/releases/tag/v1.6.3"
  },
  "what_shipped": [
    "Added `cortex journal draft <type> --facts-file <path>`.",
    "Added strict validation with structured error diagnostics."
  ],
  "downstream_docs": ["README.md", "docs/config-reference.md"],
  "cortex_refs": {
    "plans": ["context-integrity-production"],
    "spec": ["§ 4.2", "§ 7"]
  },
  "followups": []
}
```

### `incident`

```json
{
  "type": "incident",
  "title": "CI migration check failed after green run",
  "summary": "A migration script regressed and blocked release automation.",
  "context": "The failure surfaced after a dependency update.",
  "impact": "Release pipeline stalled for two hours.",
  "timeline": [
    "10:10 — first failure",
    "11:25 — root cause identified",
    "12:10 — fixed"
  ],
  "root_cause": "Ungated migration logic in test setup path.",
  "went_well": ["Alerting fired quickly."],
  "went_poorly": ["Rollback runbook omitted this path."],
  "action_items": ["Add rollback section to runbook."],
  "cortex_refs": {
    "journal": ["2026-05-12-ci-incident"]
  }
}
```

### `plan-transition`

```json
{
  "type": "plan-transition",
  "title": "Plan context-integrity-production — active → shipped",
  "plan": "context-integrity-production",
  "from_status": "active",
  "to_status": "shipped",
  "reason": "Success criteria met.",
  "outcome": "All scoped slices shipped with regression coverage.",
  "deferred_items": [
    "Add PR-merged hook facts-packet producer."
  ],
  "cortex_refs": {
    "plans": ["context-integrity-production"],
    "journal": ["2026-05-13-facts-file-track-a"]
  }
}
```

## Notes

- **JSON only** for now. YAML is rejected explicitly.
- Unknown fields are rejected (`schema is intentionally narrow`).
- `cortex_refs` allows only `plans`, `doctrine`, `spec`, and `journal` arrays.
- Driver/human remains responsible for final correctness review before commit.
